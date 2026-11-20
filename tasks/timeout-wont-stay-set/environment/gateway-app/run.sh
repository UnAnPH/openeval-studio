#!/bin/bash
set -euo pipefail

java -cp '/app/out:/app/lib/*' io.gateway.Gateway "$@"
