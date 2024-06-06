FROM cyberbotics/webots.cloud:R2023b-ubuntu22.04
# Disable dialog boxes for non-interactive installation
ARG DEBIAN_FRONTEND=noninteractive

# Update the package list and install the necessary software packages
RUN apt-get update && apt-get install --fix-broken -y \
    && apt-get install -y --no-install-recommends \
       hostapd \
       dnsmasq \
       cmake \
       libeigen3-dev \
       build-essential \
       cmake-curses-gui \
       libunittest++-dev \
       qtbase5-dev \
       tmux \
       wget \
       liburdfdom-dev \
       libtinyxml2-dev \
       sudo \
    && rm -rf /var/lib/apt/lists/*

# Create a non-root user and user group
RUN groupadd -g 1000 fftai && useradd -u 1000 -g 1000 -s /bin/bash fftai \
    && echo 'fftai ALL=(ALL) NOPASSWD: ALL' >> /etc/sudoers

# Switch to the fftai user and execute the installation command
USER fftai
WORKDIR /home/fftai

# Download the ROS packages
RUN wget --no-check-certificate -O /home/fftai/rocs-libs.deb https://rocs-1302548221.cos.ap-shanghai.myqcloud.com/libs/rocs-libs_1.1.deb \
    && wget --no-check-certificate -O /home/fftai/rocs-svr.deb https://rocs-1302548221.cos.ap-shanghai.myqcloud.com/server/rocs-svr_v2.0.10.deb

RUN wget --no-check-certificate  -O rocs-webots.deb https://rocs-1302548221.cos.ap-shanghai.myqcloud.com/webots/rocs-webots_1.3.deb

USER root
# install the packages
RUN dpkg -i /home/fftai/rocs-libs.deb \
    && dpkg -i /home/fftai/rocs-svr.deb \
    && rm /home/fftai/rocs-libs.deb /home/fftai/rocs-svr.deb

RUN dpkg -i /home/fftai/rocs-webots.deb \
    && rm /home/fftai/rocs-webots.deb

WORKDIR /

COPY rocs-svr /etc/init.d/rocs-svr

RUN chmod 755 /etc/init.d/rocs-svr
RUN chmod 755 /usr/local/bin/rocs-svr/start_rocs_svr.sh

# Uncomment the following lines if you use sysvinit
RUN update-rc.d rocs-svr defaults

EXPOSE 5005 8001 51888

# Use a script to start the services
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
CMD ["/bin/bash", "/usr/local/bin/entrypoint.sh"]