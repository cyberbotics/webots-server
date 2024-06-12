# Webots Server

This repository contains the files to be deployed on a server machine to run Webots simulations online.

The documentation on how to set-up a Webots simulation server is provided in the [Webots user guide](https://cyberbotics.com/doc/guide/web-server).

## Quick start

### Configure Apache
```
sudo a2enmod proxy proxy_http proxy_wstunnel
```
Edit `/etc/apache2/site-available/fftai.conf` and add the following lines at the end of the `VirtualHost` section:

```
<VirtualHost *:80>
    ServerName platform.fftai.top

    RewriteEngine on
    # port redirection rules (for session_server.py, simulation_server.py and webots)
    # websockets (should come first)
    RewriteCond %{HTTP:Upgrade} websocket [NC]
    RewriteCond %{HTTP:Connection} upgrade [NC]
    RewriteRule ^/(\d*)/(.*)$ "ws://localhost:$1/$2" [P,L]
    # http traffic (should come after websocket)
    RewriteRule ^/load$ "http://localhost:1999/load" [P,L]
    RewriteRule ^/monitor$ "http://localhost:1999/monitor" [P,L]
    RewriteRule ^/session$ "http://localhost:1999/session" [P,L]
    RewriteRule ^/(\d*)/(.*)$ "http://localhost:$1/$2" [P,L]

</VirtualHost>
```

For platform.fftai.top enabled SSL:

```
LoadModule proxy_module modules/mod_proxy.so
LoadModule proxy_http_module modules/mod_proxy_http.so
LoadModule proxy_wstunnel_module modules/mod_proxy_wstunnel.so

<VirtualHost *:80>
  ServerName fftai.top
  ServerAlias platform.fftai.top

  [ ... ]

  RewriteEngine on

  # this rule redirect HTTP requests to HTTPS, removing the 'www.' prefix if any
  RewriteCond %{SERVER_NAME} =%{SERVER_NAME} [OR]
  RewriteCond %{SERVER_NAME} =www.%{SERVER_NAME}
  RewriteRule ^ https://%{SERVER_NAME}%{REQUEST_URI} [END,NE,R=permanent]
</VirtualHost>

<VirtualHost *:443>
  ServerName fftai.top
  ServerAlias platform.fftai.top

  [ ... ]

  RewriteEngine on

  # this rule removes the 'www.' prefix from the hostname in the URL if any
  RewriteCond %{SERVER_NAME} =www.webserver.com
  RewriteRule ^ https://webserver.com%{REQUEST_URI} [END,NE,R=permanent]

  # port redirection rules (for session_server.py, simulation_server.py and webots)
  # websockets (should come first)
  RewriteCond %{HTTP:Upgrade} websocket [NC]
  RewriteCond %{HTTP:Connection} upgrade [NC]
  RewriteRule ^/(\d*)/(.*)$ "ws://%{SERVER_NAME}:$1/$2" [P,L]
  # http traffic (should come after websocket)
  RewriteRule ^/(\d*)/(.*)$ "http://%{SERVER_NAME}:$1/$2" [P,L]

</VirtualHost>
```

Edit `000-default.conf` to configure for webots static page:
<VirtualHost *:80>
    ServerAdmin webmaster@localhost
    DocumentRoot /var/www/html

    # Alias /wwi to /home/$user/luban-server/html/wwi
    Alias /wwi /home/$user/luban-server/html/wwi
    <Directory /home/$user/luban-server/html/wwi>
        Options Indexes FollowSymLinks
        AllowOverride None
        Require all granted
    </Directory>

    # Alias /webots to /home/$user/luban-server/html/webots
    Alias /webots /home/$user/luban-server/html/webots
    <Directory /home/$user/luban-server/html/webots>
        Options Indexes FollowSymLinks
        AllowOverride None
        Require all granted
    </Directory>

    ErrorLog ${APACHE_LOG_DIR}/error.log
    CustomLog ${APACHE_LOG_DIR}/access.log combined
</VirtualHost>

### Start Luban Server
```
pip install pynvml requests psutil tornado distro
./server.sh start fftai
```

## Run Webots in Docker

Need to execute 
```
xhost +
```
or 
```
xhost +local:docker
on the host 
```