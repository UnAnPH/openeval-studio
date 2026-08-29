#!/bin/bash
set -euo pipefail

javac -d /app/out -cp '/app/lib/*' $(find /app/src -name '*.java')
