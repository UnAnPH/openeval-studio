/* extract.c — reference extractor for the .archive format.
 *
 * Reads a VALID .archive file, validates its structure and checksums, and
 * writes each contained file into the output directory.
 *
 *   usage: extract <archive> <output-dir>
 *
 * This binary ships to /app/extract (compiled + stripped). It is the only
 * description of the format the agent is given, so the repair tool must be
 * reverse-engineered from it. It also doubles as the agent's self-check:
 * a correct repair extracts cleanly; any corruption makes it refuse.
 *
 * Layout (all integers little-endian, offsets absolute from start of file):
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <sys/types.h>
#include <sys/stat.h>

#define FOOTER_SIZE 14
#define MAGIC 0xA39CB251

#define GF_POLY 0x165
#define RS_K    239
#define RS_NPAR 4
#define RS_FCR  0

static uint8_t gf_exp[512], gf_log[256], rs_gen[RS_NPAR + 1];

static void rs_init(void) {
    unsigned x = 1;
    for (int i = 0; i < 255; i++) {
        gf_exp[i] = (uint8_t)x;
        gf_log[x] = (uint8_t)i;
        x <<= 1;
        if (x & 0x100) x ^= GF_POLY;
        x &= 0xFF;
    }
    for (int i = 255; i < 512; i++) gf_exp[i] = gf_exp[i - 255];

    rs_gen[0] = 1;
    for (int i = 1; i <= RS_NPAR; i++) rs_gen[i] = 0;
    for (int i = 0; i < RS_NPAR; i++) {
        uint8_t root = gf_exp[(RS_FCR + i) % 255];
        for (int j = i + 1; j > 0; j--)
            rs_gen[j] ^= (rs_gen[j - 1] && root)
                ? gf_exp[gf_log[rs_gen[j - 1]] + gf_log[root]] : 0;
    }

}

static inline uint8_t gf_mul(uint8_t a, uint8_t b) {
    if (!a || !b) return 0;
    return gf_exp[gf_log[a] + gf_log[b]];
}

static void rs_encode_chunk(const uint8_t *data, size_t len, uint8_t out[RS_NPAR]) {
    uint8_t par[RS_NPAR] = {0};
    for (size_t i = 0; i < len; i++) {
        uint8_t coef = data[i] ^ par[0];
        for (int j = 0; j < RS_NPAR - 1; j++)
            par[j] = par[j + 1] ^ gf_mul(rs_gen[j + 1], coef);
        par[RS_NPAR - 1] = gf_mul(rs_gen[RS_NPAR], coef);
    }
    memcpy(out, par, RS_NPAR);
}

static uint16_t rd_u16(const unsigned char *p) {
    return (uint16_t)(p[0] | (p[1] << 8));
}
static uint32_t rd_u32(const unsigned char *p) {
    return (uint32_t)(p[0] | (p[1] << 8) | (p[2] << 16) | ((uint32_t)p[3] << 24));
}

/* Standard CRC-32 (poly 0xEDB88320, init/xorout 0xFFFFFFFF) — the zlib/ZIP CRC. */
static uint32_t crc32_buf(const unsigned char *data, size_t n) {
    uint32_t crc = 0xFFFFFFFFu;
    for (size_t i = 0; i < n; i++) {
        crc ^= data[i];
        for (int k = 0; k < 8; k++) {
            if (crc & 1u) crc = (crc >> 1) ^ 0xEDB88320u;
            else          crc >>= 1;
        }
    }
    return crc ^ 0xFFFFFFFFu;
}

static inline uint32_t rotl32(uint32_t x, unsigned n) {
    return (uint32_t)((x << n) | (x >> (32 - n)));
}
static inline uint32_t rotr32(uint32_t x, unsigned n) {
    return (uint32_t)((x >> n) | (x << (32 - n)));
}

/* Per-entry digest stored in the CD. */
static inline uint32_t digest_hashA(const uint8_t *data, size_t len) {
    uint32_t a = 0xAB9302CFu;
    uint32_t b = 0x1B873593u;
    for (size_t i = 0; i < len; i++) {
        uint8_t byte = data[i];
        a = a + byte;
        a = rotl32(a, 7);
        b = b ^ a;
        b = b * 0x85EBCA6Bu;
        a = a + (b >> 13);
    }
    a ^= b;
    a = a ^ (a >> 16);
    a = a * 0xC2B2AE35u;
    a = a ^ (a >> 15);
    return a;
}

/* Checksum over the central-directory bytes.*/
static inline uint32_t master_check(const uint8_t *data, size_t len) {
    uint32_t h = 0x1357BD13u;
    uint32_t acc = 0;
    for (size_t i = 0; i < len; i++) {
        uint8_t byte = data[i];
        acc = acc + (uint32_t)(byte + 1) * (uint32_t)(i + 1);
        h ^= (uint32_t)(byte * 0x9E3779B1u);
        h = rotr32(h, 17);
        h = h + acc + byte;
    }
    h = h ^ (h << 13);
    h = h + acc * 0x2545F491u;
    return h;
}

static void die(int code) {
    exit(code);
}

int main(int argc, char **argv) {
    rs_init();
    if (argc != 3) {
        fprintf(stderr, "usage: %s <archive> <output-dir>\n", argv[0]);
        return 2;
    }

    /* slurp the whole archive into memory */
    FILE *f = fopen(argv[1], "rb");
    if (!f) die(1);
    if (fseek(f, 0, SEEK_END) != 0) die(2);
    long sz = ftell(f);
    if (sz < 0) die(3);
    rewind(f);
    size_t len = (size_t)sz;
    unsigned char *buf = malloc(len ? len : 1);
    if (!buf) die(4);
    if (fread(buf, 1, len, f) != len) die(5);
    fclose(f);

    if (len < 4 + FOOTER_SIZE) die(6);
    if (rd_u32(buf) != MAGIC) die(7);

    /* footer: last 14 bytes */
    size_t foot = len - FOOTER_SIZE;
    if (buf[foot] != 'E' || buf[foot + 1] != 'D') die(8);
    uint32_t dir_offset   = rd_u32(buf + foot + 2);
    uint32_t count        = rd_u32(buf + foot + 6);
    uint32_t stored_check = rd_u32(buf + foot + 10);

    if (dir_offset > foot) die(9);

    /* the directory must verify against the footer's master_check */
    if (master_check(buf + dir_offset, foot - dir_offset) != stored_check)
        die(10);

    (void)mkdir(argv[2], 0777);   /* ok if it already exists */

    /* walk the central directory; pull each file's data from its local record */
    size_t p = dir_offset;
    for (uint32_t i = 0; i < count; i++) {
        if (p + 2 > foot || buf[p] != 'C' || buf[p + 1] != 'D') die(11);
        p += 2;
        if (p + 8 > foot) die(12);
        uint32_t local_off = rd_u32(buf + p); p += 4;
        uint16_t name_len  = rd_u16(buf + p); p += 2;
        uint32_t data_len  = rd_u32(buf + p); p += 4;
        uint32_t cd_crc    = rd_u32(buf + p); p += 4;
        uint32_t cd_hashA  = rd_u32(buf + p); p += 4;
         uint16_t nchunks   = rd_u16(buf + p); p += 2;        /* NEW */
        const unsigned char *parity = buf + p;               /* NEW */
        if (p + (size_t)nchunks * RS_NPAR > foot) die(13);
        p += (size_t)nchunks * RS_NPAR;                      /* NEW */
        if (p + name_len > foot) die(14);
        const unsigned char *name = buf + p; p += name_len;

        /* find and validate the matching local record */
        size_t q = local_off;
        if (q + 2 > len || buf[q] != 'L' || buf[q + 1] != 'R') die(15);
        q += 2;
        if (q + 10 > len) die(16);
        uint16_t lr_name_len = rd_u16(buf + q); q += 2;
        uint32_t lr_data_len = rd_u32(buf + q); q += 4;
        uint32_t lr_crc      = rd_u32(buf + q); q += 4;
        if (q + lr_name_len > len) die(17);
        const unsigned char *lr_name = buf + q; q += lr_name_len;
        if (q + lr_data_len > len) die(18);
        const unsigned char *data = buf + q;

        /* the directory entry and the local record must agree */
        if (lr_data_len != data_len || lr_name_len != name_len ||
            memcmp(lr_name, name, name_len) != 0)
            die(19);
        /* per-file integrity */
        if (crc32_buf(data, data_len) != cd_crc || lr_crc != cd_crc)
            die(20);

        /* check the custom digest hashA */
        if (digest_hashA(data, data_len) != cd_hashA)
            die(21);

        uint16_t want_chunks = (uint16_t)((data_len + RS_K - 1) / RS_K);
        if (nchunks != want_chunks) die(22);
        for (uint16_t c = 0; c < nchunks; c++) {
            size_t off = (size_t)c * RS_K;
            size_t clen = (data_len - off < RS_K) ? (data_len - off) : RS_K;
            uint8_t want[RS_NPAR];
            rs_encode_chunk(data + off, clen, want);
            if (memcmp(want, parity + (size_t)c * RS_NPAR, RS_NPAR) != 0)
                die(23);
        }

        /* write <out-dir>/<name> (flat names only) */
        if (name_len == 0) die(24);
        if (memchr(name, '/', name_len) != NULL) die(25);
        char path[4096];
        int wn = snprintf(path, sizeof path, "%s/%.*s", argv[2], (int)name_len, name);
        if (wn < 0 || (size_t)wn >= sizeof path) die(26);
        FILE *of = fopen(path, "wb");
        if (!of) die(27);
        if (data_len && fwrite(data, 1, data_len, of) != data_len) die(28);
        fclose(of);
    }

    free(buf);
    printf("extracted %u file(s)\n", count);
    return 0;
}
