# Task
Build a command line program at /app/repair which repairs corrupted archives. It should be invoked with /app/repair <corrupted-archive.archive> <output-directory>. This is a custom archive structure, and you can reverse engineer how it works from the extractor script at /app/extract.

# Program I/O
## Input
- The input will be an archive which is corrupted in one of the following manners:
    - Zeroed out: The trailer bytes are overwritten with zeros.
    - Truncated: The file is cut off at the end.
    - Scrambled: Bytes in the CD and footer have been reordered.
    - Data Corruption: Some bytes in the archived contents are affected by the corruption. Never more than the archive's built in redundancy can recover.
- Exactly one kind will apply to any given input. Never more, and never less.
- For each of the first three corruption types ("Zeroed out", "Truncated", "Scrambled"), the data is never affected and only the trailer bytes are. Inversely, for "Data Corruption" the trailer bytes are intact but the data is affected.

## Output
- The output must be an extractable archive file in <output-directory>/recovered-archive.archive containing the recovered contents.
- The output must be identical to what it would have looked like before corruption down to each byte.

# Constraints
- The program must be deterministic: If I put the same corrupted file through it twice, I should get the same output
- The program must work for all of the above mentioned corruptions.
- The program must successfully recover the archive within a 60 second timeout.
- The program must return 0 for success, and 1 for failure.
