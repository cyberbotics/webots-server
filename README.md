# Webots Server

This repository contains the files to be deployed on a server machine to run Webots simulations online.

The documentation on how to set-up a Webots simulation server is provided in the [Webots user guide](https://cyberbotics.com/doc/guide/web-server).

## Quick start

pip install pynvml requests psutil tornado distro
./server.sh start fftai

# Run Webots in Docker

Need to execute 
xhost +
or 
xhost +local:docker
on the host 