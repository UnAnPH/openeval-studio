`/app/bin/fetch-file` is the command-line entry point.

The implementation lives in `/app/src/downloader.py`. It currently performs only a simple full download and does not safely resume interrupted transfers.
