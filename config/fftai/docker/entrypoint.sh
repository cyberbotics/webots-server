#!/bin/bash
set -e

# Start the service
service rocs-svr start

# Keep the container running
tail -f /dev/null