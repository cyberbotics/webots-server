#!/usr/bin/env python3

# Copyright 1996-2022 Cyberbotics Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Webots simulation server."""

from async_process import AsyncProcess
from pynvml import nvmlInit, nvmlShutdown, nvmlDeviceGetHandleByIndex, nvmlDeviceGetName, nvmlDeviceGetMemoryInfo, \
    nvmlDeviceGetUtilizationRates, NVMLError

import asyncio
import errno
import json
import logging
import os
import psutil
import re
import requests
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import threading
import tornado.ioloop
import tornado.httpserver
import tornado.web
import tornado.websocket
import traceback

if sys.platform == 'win32':
    import wmi
elif sys.platform == 'darwin':
    import platform
else:  # assuming linux
    import distro


SNAPSHOT_REFRESH = 1  # make a performance measurement every second
docker_compose_cmd = 'docker compose'
network_sent = 0
network_received = 0


def expand_path(path):
    """Expand user and environmental variables in a string."""
    return os.path.expandvars(os.path.expanduser(path))


def mkdir_p(path):
    """Create a directory if it doesn't exit."""
    try:
        os.makedirs(path)
    except OSError as exc:
        if exc.errno == errno.EEXIST and os.path.isdir(path):
            pass
        else:
            raise


def chmod_python_and_executable_files(directory):
    """Add executable permissions to Python and executable files."""
    if sys.platform == 'win32':
        return
    for filename in os.listdir(directory):
        fullname = os.path.join(directory, filename)
        if os.path.isdir(fullname):
            chmod_python_and_executable_files(fullname)
        if filename.endswith('.py') or not os.path.splitext(filename)[1]:
            os.chmod(fullname, 0o775)


class Snapshot:
    """This class stores instantaneous monitoring information on the machine."""

    def __init__(self):
        """Create an empty instance of MonitorSnapshot."""
        self.data = {'Timestamp': 0,
                     'Webots running': 0,
                     'Webots idle': 0,
                     'CPU load': 0,
                     'CPU memory': 0,
                     'GPU load compute': 0,
                     'GPU load memory': 0,
                     'GPU memory': 0,
                     'Swap': 0,
                     'Disk': 0,
                     'Network sent': 0,
                     'Network received': 0}

    def write(self):
        """Save a snapshot into a file."""
        if not config['monitorLogEnabled']:
            return
        global monitorFile
        file = open(monitorFile, 'a')
        file.write(f'{self.data["Timestamp"]}, ')
        file.write(f'{self.data["Webots running"]}, ')
        file.write(f'{self.data["Webots idle"]}, ')
        file.write(f'{self.data["CPU load"]}, ')
        file.write(f'{self.data["CPU memory"]}, ')
        file.write(f'{self.data["GPU load compute"]}, ')
        file.write(f'{self.data["GPU load memory"]}, ')
        file.write(f'{self.data["GPU memory"]}, ')
        file.write(f'{self.data["Swap"]}, ')
        file.write(f'{self.data["Disk"]}, ')
        file.write(f'{self.data["Network sent"]}, ')
        file.write(f'{self.data["Network received"]}\n')
        file.close()


class Client:
    """This class represents an instance of connected client."""

    def __init__(self, websocket=None):
        """Create an instance of client."""
        self.websocket = websocket
        self.streaming_server_port = 0
        self.rocs_server_port = 0
        self.webots_process = None
        self.on_webots_quit = None
        self.project_instance_path = ''
        self.app = ''
        self.world = ''
        self.idle = True
        self.competition = False

    def __del__(self):
        """Destroy an instance of client."""
        if self.websocket:
            self.websocket.close()
        self.kill_webots()
        self.cleanup_webots_instance()

    def setup_project(self):
        global config
        global current_load
        base_instance_path = os.path.join(config['instancesPath'], str(id(self)))
        self.project_instance_path = base_instance_path  # Update this later to include repo folder

        if not hasattr(self, 'url'):
            logging.error('Missing URL.')
            return False
        
        if not self.url.startswith('https://github.com/'):
            logging.error(f'Unsupported URL protocol: {self.url}')
            return False
        
        if 'allowedRepositories' not in config or not self.url.startswith(tuple(config['allowedRepositories'])):
            if not config['docker']:
                error = 'Docker not enabled: cannot host simulations from repositories outside of "allowedRepositories".'
            elif config['shareIdleTime'] == 0:
                error = 'This simulation server is not configured to share idle time.'
            elif current_load > config['shareIdleTime']:
                error = f'Cannot share idle time when current load is above threshold: {current_load}.'
            elif 'blockedRepositories' in config and self.url.startswith(tuple(config['blockedRepositories'])):
                error = f'Cannot run simulation from blocked repository: {self.url}'
            else:
                error = False

            if error:
                self.websocket.write_message(f'loading: Error: {error}')
                logging.error(error)
                return False

        parts = self.url[19:].split('/')
        username = parts[0]
        repository_name = parts[1]
        length = len(parts)
        folder = '/'.join(parts[4:length - 2])
        
        # Construct the cache path from the username and repository name.
        cache_path = os.path.join(config['projectCacheDir'], username, repository_name)
        if folder:
            cache_path = os.path.join(cache_path, folder)

        # Check if the cache path exists.
        if not os.path.exists(cache_path):
            # If not, clone the repository from GitHub.
            if not self.setup_project_from_github():
                return False
            # Copy the cloned project to the cache directory.
            try:
                # Remove old cache if any
                if os.path.exists(cache_path):
                    shutil.rmtree(cache_path)
                
                # Create the necessary directories for the cache path
                parent_directory = os.path.dirname(cache_path)
                os.makedirs(parent_directory, exist_ok=True)

                
                # Copy the project folder
                shutil.copytree(self.project_instance_path, cache_path)
                logging.info(f'Project {repository_name} cached at {cache_path}')
            except Exception as e:
                logging.error(f'Error copying project to cache: {e}')
                return False
        else:
            try:
                self.world = parts[-1]
                # Copy the cached project to the project instance path.
                self.project_instance_path = os.path.join(base_instance_path, repository_name, *parts[4:-2])
                if os.path.exists(self.project_instance_path):
                    shutil.rmtree(self.project_instance_path)
                # Create the necessary directories for the cache path
                parent_directory = os.path.dirname(self.project_instance_path)
                os.makedirs(parent_directory, exist_ok=True)
                shutil.copytree(cache_path, self.project_instance_path)
                logging.info(f'Project {repository_name} copied from cache to {self.project_instance_path}')
                # Set the project path to the project instance path.
                os.chdir(base_instance_path)
            except Exception as e:
                logging.error(f'Error copying project from cache: {e}')
                return False
        
        return True

    def setup_project_from_github(self):
        parts = self.url[19:].split('/')
        length = len(parts)

        if length < 6:
            error = 'Wrong Webots simulation URL'
        else:
            username = parts[0]
            repository = parts[1]
            if parts[2] != 'blob' and parts[2] != 'tree':
                error = f'Missing blob in Webots simulation URL, found: {parts[2]}'
            else:
                version = parts[3]  # tag or branch name
                folder = '/'.join(parts[4:length - 2])
                if parts[length - 2] != 'worlds':
                    error = 'Missing worlds folder in Webots simulation URL'
                else:
                    filename = parts[length - 1]
                    if filename[-4:] != '.wbt':
                        error = 'Missing world file in Webots simulation URL'
                    else:
                        error = False

        if error:
            self.websocket.write_message(f'loading: Error: {error}')
            logging.error(error)
            return False

        # Cloning the project repository.
        self.world = filename
        mkdir_p(self.project_instance_path)
        os.chdir(self.project_instance_path)
        repository_url = f'https://git@github.com/{username}/{repository}.git'
        try:
            path = os.getcwd()
        except OSError:
            path = False
        # We use sparse checkout when the world file is inside a subfolder of the repository.
        if folder:
            sparse_set_command = f'git sparse-checkout set "/{folder}"'
        else:
            sparse_set_command = 'echo "Cloning the whole repository, no need to set sparse checkout"'
        # We only clone the given branch/tag of the repository.
        command = AsyncProcess([
            'bash', '-c',
            f'git clone --depth=1 --no-checkout --branch "{version}" "{repository_url}" && '
            f'cd "{repository}" && {sparse_set_command} && git checkout "{version}"'
        ])
        logging.info(f'$ shallow and sparse git clone in repository {repository_url} on branch {version}')
        while True:
            output = command.run()
            if output[0] == 'x':
                break
            if output[0] == '2':  # stderr
                logging.error(output[1:].strip('\n'))
            else:  # stdout
                logging.info(output[1:].strip('\n'))

        self.project_instance_path += f'/{repository}'
        if folder:
            self.project_instance_path += f'/{folder}'
        logging.info('Done')
        if path:
            os.chdir(path)

        return True

    def cleanup_webots_instance(self):
        """Cleanup the local Webots project not used any more by the client."""
        if id(self) and os.path.exists(config['instancesPath'] + str(id(self))):
            shutil.rmtree(config['instancesPath'] + str(id(self)))

    def start_webots(self, on_webots_quit):
        """Start a Webots instance in a separate thread."""

        def runWebotsInThread(client):
            global config
            global docker_compose_cmd

            world = f'{self.project_instance_path}/worlds/{self.world}'
            port = client.streaming_server_port
            asyncio.set_event_loop(asyncio.new_event_loop())
            if not os.path.exists(world):
                error = f"Error: {self.world} does not exist."
                logging.error(f'loading: {error}')
                client.websocket.write_message(error)
                return

            webotsCommand = f'{config["webots"]} --batch --mode=pause '
            if not hasattr(self, 'mode') or self.mode == 'x3d':  # the MJPEG stream won't work if the Webots window is minimized
                webotsCommand += '--minimize --no-rendering '
            webotsCommand += f'--port={port} --heartbeat=5000 '
            webotsCommand += f'--stream={self.mode} ' if hasattr(self, 'mode') else '--stream '

            if config['docker']:
                # create environment variables
                os.chdir(self.project_instance_path)
                with open(world) as world_file:
                    version = world_file.readline().split()[1]
                webots_default_image = f'cyberbotics/webots.cloud:{version}-ubuntu'
                if version[0] == 'R' and (int(version[1:5]) < 2023 or version == "R2023a"):
                    webots_default_image += '20.04'
                else:
                    webots_default_image += '22.04'
                makeProject = int(os.path.isfile('Makefile'))
                webotsCommand = '\"' + webotsCommand.replace('\"', '\\"') + f'{config["projectsDir"]}/worlds/{self.world}\"'
                envVarDocker = {
                    "IMAGE": webots_default_image,
                    "PROJECT_PATH": config["projectsDir"],
                    "MAKE": makeProject,
                    "PORT": port,
                    "ROCS_PORT": client.rocs_server_port,
                    "COMPOSE_PROJECT_NAME": str(id(self)),
                    "WEBOTS": webotsCommand
                }
                if 'SSH_CONNECTION' in os.environ:
                    xauth = f'/tmp/.docker-{port}.xauth'
                    os.system('touch ' + xauth)
                    display = os.environ['DISPLAY']
                    os.system(f"xauth nlist {display} | sed -s 's/^..../ffff/' | xauth -f {xauth} nmerge -")
                    os.system(f'chmod 777 {xauth}')
                    envVarDocker["DISPLAY"] = display
                    envVarDocker["XAUTHORITY"] = xauth

                config['dockerConfDir'] = os.path.abspath(os.path.dirname(__file__)) + '/config/simulation/docker'
                # create a Dockerfile if not provided in the project folder
                defaultDockerfilePath = ''
                if not os.path.isfile('Dockerfile'):
                    defaultDockerfilePath = config['dockerConfDir'] + '/Dockerfile.default'
                    if os.path.exists(defaultDockerfilePath):
                        os.system(f'ln -s {defaultDockerfilePath} ./Dockerfile')
                    else:
                        error = f"Error: Missing Dockerfile.default in {config['dockerConfDir']}"
                        logging.error(error)
                        client.websocket.write_message(f'loading: {error}')
                        return

                # create a docker-compose.yml and get simulation type
                dockerComposePath = ''
                for yamlFileName in ['webots.yml', 'webots.yaml']:
                    if os.path.exists(yamlFileName):
                        with open(yamlFileName, 'r') as webotsYml_file:
                            data = webotsYml_file.read().splitlines(True)
                        for line in data:
                            if line.startswith("dockerCompose:"):
                                info = line.split(':')
                                if info[1].strip().startswith("theia"):
                                    volume = info[2]
                                    dockerComposePath = config['dockerConfDir'] + "/docker-compose-theia.yml"
                                    envVarDocker["THEIA_VOLUME"] = volume
                                    envVarDocker["THEIA_PORT"] = port + 500
                                    client.websocket.write_message('ide: enable')
                                elif info[1].strip() == 'competition':
                                    client.competition = True
                                    volume = info[2]
                                    dockerComposePath = config['dockerConfDir'] + "/docker-compose-competition.yml"
                                    envVarDocker["THEIA_VOLUME"] = volume
                                    envVarDocker["THEIA_PORT"] = port + 500
                                    client.websocket.write_message('ide: enable')
                                    # using hard link so that the COPY command in the Dockerfile will work on the launcher file
                                    os.system(f'ln {config["dockerConfDir"]}/remote_controller_launcher.py')
                                    with open(world, 'r') as world_file:
                                        world_content = world_file.read()
                                    world_content = world_content.replace('controller "participant"', 'controller "<extern>"')
                                    with open(world, 'w') as world_file:
                                        world_file.write(world_content)
                            elif line.strip().startswith("type:"):
                                message = line.replace(" ", "")
                                client.websocket.write_message(message)
                        break

                if not os.path.exists(dockerComposePath):
                    dockerComposePath = config['dockerConfDir'] + "/docker-compose-default.yml"

                if os.path.exists(dockerComposePath):
                    os.system(f'ln -s {dockerComposePath} ./docker-compose.yml')
                else:
                    error = f"Error: Missing docker-compose-default.yml in {config['dockerConfDir']}"
                    logging.error(error)
                    client.websocket.write_message(f'loading: {error}')
                    return
                logging.info(f'docker-compose.yml created from {dockerComposePath}')

                # create a .env file
                with open('.env', 'w') as env_file:
                    for key, value in envVarDocker.items():
                        env_file.write(f'{key}={value}\n')

                command = f'{docker_compose_cmd} -f {self.project_instance_path}/docker-compose.yml up --build --no-color'
            else:
                webotsCommand += world
                command = webotsCommand
            try:
                command = f"timeout {config['timeout']} " + command
                client.webots_process = subprocess.Popen(command.split(),
                                                         stdout=subprocess.PIPE,
                                                         stderr=subprocess.STDOUT,
                                                         bufsize=1, universal_newlines=True)
                client.websocket.write_message(f"shutdownTimeout: {config['timeout']}")
            except Exception:
                error = f"Error: Unable to start Webots: {webotsCommand}"
                logging.error(error)
                client.websocket.write_message(f'loading: {error}')
                return
            logging.info(f'[{id(client)}] Webots [{client.webots_process.pid}] started: "{webotsCommand}"')
            while True:
                if client.webots_process is None:
                    logging.warning('Client connection closed or killed')
                    # client connection closed or killed
                    return
                line = client.webots_process.stdout.readline().rstrip()
                if config['docker']:
                    if line:
                        logging.info(line)
                        if not ("theia" in line):
                            client.websocket.write_message(f'loading: {line}')
                        if defaultDockerfilePath and "not found" in line:
                            client.websocket.write_message(
                                f"loading: Error: Image version {version} not available on Cyberbotics' dockerHub. "
                                f"Please, add the appropriate Dockerfile to your project.")
                            return
                    if '|' in line:  # docker compose format
                        line = line[line.index('|') + 2:]
                if line.startswith('.'):  # Webots world is loaded, ready to receive connections
                    logging.info('Webots world is loaded, ready to receive connections')
                    break
            hostname = config['server']
            protocol = 'wss:' if config['ssl'] else 'ws:'
            separator = '/' if config['portRewrite'] else ':'
            message = f'webots:{protocol}//{hostname}{separator}{port}'
            client.websocket.write_message(message)
            for line in iter(client.webots_process.stdout.readline, b''):
                if client.webots_process is None:
                    break
                line = line.rstrip()
                if line.startswith(f'{str(id(self))}-webots-1'):  # output from docker compose's webots service
                    output = line[line.index('|') + 2:]
                    if output == '.':
                        client.websocket.write_message('.')
                    elif output == 'pause':
                        client.idle = True
                    elif output == 'real-time' or output == 'step':
                        client.idle = False
                    elif client.competition and output == 'reset':
                        subprocess.Popen([
                            'docker', 'compose', '-f', f'{self.project_instance_path}/docker-compose.yml',
                            'restart', '--timeout', '0', 'controller'])
            client.on_exit()

        if self.setup_project():
            self.on_webots_quit = on_webots_quit
            threading.Thread(target=runWebotsInThread, args=(self,)).start()
        else:
            on_webots_quit()

    def on_exit(self):
        """Callback issued when Webots quits."""
        if self.webots_process:
            logging.warning(f'[{id(self)}] Webots [{self.webots_process.pid}] exited')
            self.webots_process.wait()
            self.webots_process = None
        self.on_webots_quit()

    def kill_webots(self):
        """Force the termination of Webots or relative Docker service(s)."""
        global docker_compose_cmd
        if config['docker']:
            if os.path.exists(f"{self.project_instance_path}/docker-compose.yml"):
                os.system(f"{docker_compose_cmd} -f {self.project_instance_path}/docker-compose.yml down "
                          "-v --timeout 0")

            if self.webots_process:
                self.webots_process.terminate()
                try:
                    self.webots_process.wait(10)  # set a timeout (seconds) to avoid blocking the whole script
                except subprocess.TimeoutExpired:
                    logging.warning(f'[{id(self)}] Error killing Webots [{self.webots_process.pid}]')
                    self.webots_process.kill()
                self.webots_process = None

            # Remove old images if there is a 'persistantDockerImages' list in the config file
            if 'persistantDockerImages' in config:
                # Get list of all images
                output = subprocess.check_output(['docker', 'images', '--format', '{{json . }}'])
                output = output.decode()

                # Split output into individual JSON objects
                image_strings = output.strip().split('\n')
                images = []
                for image_string in image_strings:
                    images.append(json.loads(image_string))
                current_time = time.time()

                for image in images:
                    repository = image['Repository']
                    if "webots" in repository:
                      tag = image['Tag']
                      created_at = image['CreatedAt']
                      created_at = time.mktime(time.strptime(created_at, '%Y-%m-%d %H:%M:%S %z %Z'))
                      # Check if image is not in use by any running containers and if it was created more than 24 hours ago
                      output = subprocess.check_output(['docker', 'ps', '-q', '-f', f'ancestor={repository}:{tag}'])
                      if (output == b'' and (current_time - created_at) > 1 * 24 * 60 * 60
                              and f'{repository}:{tag}' not in config['persistantDockerImages']):
                          subprocess.call(['docker', 'rmi', f'{repository}:{tag}'])
            os.system("docker system prune --volumes -f")
        else:
            if self.webots_process:
                logging.warning(f'[{id(self)}] Webots [{self.webots_process.pid}] was killed')
                if sys.platform == 'darwin':
                    self.webots_process.kill()
                else:
                    self.webots_process.terminate()
                    try:
                        self.webots_process.wait(5)  # set a timeout (seconds) to avoid blocking the whole script
                    except subprocess.TimeoutExpired:
                        logging.warning(f'[{id(self)}] Error killing Webots [{self.webots_process.pid}]')
                        self.webots_process.kill()
                self.webots_process = None


class ClientWebSocketHandler(tornado.websocket.WebSocketHandler):
    """This class handles websocket connections."""

    clients = set()

    def check_origin(self, origin):
        """Allow to run the server on the same computer as the client."""
        return True

    @classmethod
    def find_client_from_websocket(self, websocket):
        """Return client associated with a websocket."""
        for client in self.clients:
            if client.websocket == websocket:
                return client
        return None

    @classmethod
    def next_available_port(self):
        """Return a port number available for a new Webots WebSocket server."""
        port = config['port'] + 1
        while True:
            if port > config['port'] + config['maxConnections']:
                logging.error(f'Too many open connections (>{config["maxConnections"]})')
                return 0
            found = False
            for client in self.clients:
                if port == client.streaming_server_port:
                    found = True
                    break
            if found:
                port += 2
                continue
            # try to create a server to make sure that port is available
            testSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                testSocket.bind(('0.0.0.0', port))
                found = True
            except socket.error as e:
                found = False
                if e.errno == errno.EADDRINUSE:
                    logging.info(f'Port {port} is already in use.')
                else:  # something else raised the socket.error exception
                    logging.info(f'Port {port}: {e}')
            finally:
                testSocket.close()
                if found:
                    return port
                port += 2

    def open(self):
        """Open a new connection for an incoming client."""
        self.set_nodelay(True)
        logging.info(self.request.host)
        client = Client(websocket=self)
        ClientWebSocketHandler.clients.add(client)
        logging.info(f'[{id(client)}] New client')

    def on_close(self):
        """Close connection after client leaves."""
        def async_kill_client(self, client):
            if client:
                logging.info(f'[{id(client)}] Client disconnected')
                client.kill_webots()
                if config['docker']:
                    client.cleanup_webots_instance()
                if client in ClientWebSocketHandler.clients:
                    ClientWebSocketHandler.clients.remove(client)
                    del client

        client = ClientWebSocketHandler.find_client_from_websocket(self)
        threading.Thread(target=async_kill_client, args=(self, client,)).start()

    def on_message(self, message):
        """Receive message from client."""
        client = ClientWebSocketHandler.find_client_from_websocket(self)
        if client:
            data = json.loads(message)
            if 'reset controller' in data:
                relativeFilename = f'/controllers/{data["reset controller"]}'
                shutil.copyfile(f'{config["projectsDir"]}/{client.app}{relativeFilename}',
                                f'{client.project_instance_path}/{relativeFilename}')
                self.write_message(f'reset controller: {data["reset controller"]}')
                logging.info(f'[{id(client)}] Reset file {data["reset controller"]} '
                             f'(remote ip: {self.request.remote_ip}, '
                             f'streaming_server_port: {client.streaming_server_port})')
            elif 'start' in data:  # checkout a github folder and run a simulation in there
                client.streaming_server_port = ClientWebSocketHandler.next_available_port()
                client.rocs_server_port = client.streaming_server_port + 1
                client.url = data['start']['url']
                if 'mode' in data['start']:
                    client.mode = data['start']['mode']
                    if client.mode not in ['x3d', 'mjpeg']:
                        logging.warning(f'Unsupported client mode: {client.mode}')
                logging.info(f'Starting simulation from {client.url}')
                self.start_client()

    def on_webots_quit(self):
        """Cleanup websocket connection."""
        client = ClientWebSocketHandler.find_client_from_websocket(self)
        if client and client.websocket:
            client.websocket.close()

    def start_client(self):
        """Start Webots."""
        # let 10 seconds to start Webots
        self.last_supervisor_activity = None
        client = ClientWebSocketHandler.find_client_from_websocket(self)
        client.start_webots(self.on_webots_quit)


class LoadHandler(tornado.web.RequestHandler):
    """Handle load requests."""

    def get(self):
        """Return the current load of the simulation server."""
        global previous_loads
        average_load = 0
        for load in previous_loads:
            average_load += load
        average_load /= len(previous_loads)
        self.write(str(average_load))


class MonitorHandler(tornado.web.RequestHandler):
    """Display the monitor web page."""

    global config
    global snapshots
    global nvidia

    def get(self):
        """Write the web page content."""
        def percent(value):
            level = 150 + value
            if value <= 50:
                red = '%0.2x' % int(level * value / 50)
                green = '%0.2x' % int(level)
            else:
                red = '%0.2x' % int(level)
                green = '%0.2x' % int(level - level * (value - 50) / 50)
            return f'<font color="#{red}{green}00">{value}%</font>'
        global cpu_load
        global gpu_load_compute
        global gpu_ram_usage
        global swap
        memory = psutil.virtual_memory()
        swap = psutil.swap_memory()
        if nvidia:
            nvmlHandle = nvmlDeviceGetHandleByIndex(0)
            gpu = nvmlDeviceGetName(nvmlHandle)
            if isinstance(gpu, bytes):
                gpu = gpu.decode('utf-8')
            gpu_memory = nvmlDeviceGetMemoryInfo(nvmlHandle)
            gpu_ram = round(gpu_memory.total / (1024 * 1048576), 2)
            gpu += f' - {gpu_ram}GB'
        else:
            gpu = 'Not recognized'
        ram = str(int(round(float(memory.total) / (1024 * 1048576)))) + 'GB'
        ram += f' &mdash; <b>swap:</b> {percent(swap.percent)}'
        ram += f' of {int(round(float(swap.total) / (1024 * 1048576)))}GB'
        real_cores = psutil.cpu_count(False)
        cores_ratio = int(psutil.cpu_count(True) / real_cores)
        cores = f' ({cores_ratio}x {real_cores} cores)'
        if sys.platform.startswith('linux'):
            distribution = distro.linux_distribution()
            os_name = f'Linux {distribution[0]} {distribution[1]} {distribution[2]}'
            command = 'cat /proc/cpuinfo'
            all_info = subprocess.check_output(command, shell=True).decode('utf-8').strip()
            for line in all_info.split('\n'):
                if 'model name' in line:
                    cpu = re.sub('.*model name.*:', '', line, 1)
                    break
        elif sys.platform == 'win32':
            computer = wmi.WMI()
            os_info = computer.Win32_OperatingSystem()[0]
            cpu = computer.Win32_Processor()[0].Name
            os_name = os_info.Name.split('|')[0] + ', version ' + os_info.Version
        elif sys.platform == 'darwin':
            os_name = 'macOS ' + platform.mac_ver()[0]
            os.environ['PATH'] = os.environ['PATH'] + os.pathsep + '/usr/sbin'
            command = 'sysctl -n machdep.cpu.brand_string'
            cpu = subprocess.check_output(command).strip()
        else:  # unknown platform
            os_name = 'Unknown'
            cpu = 'Unknown'
        self.write('<!DOCTYPE html>\n')
        self.write('<html><head><meta charset="utf-8"/><title>Webots simulation server</title>')
        self.write('<link rel="stylesheet" type="text/css" href="https://cyberbotics.com/wwi/R2022b/css/monitor.css">')
        self.write('</head>\n<body><h1>')
        if 'title' in config:
            self.write(config['title'])
        else:
            self.write('Webots simulation server')
        self.write('</h1>')
        if 'description' in config:
            self.write(f'<p>{config["description"]}</p>')
        self.write(f'<h2>Current load: {percent(current_load)}</h2>')
        self.write(f'<p><b>Host:</b> {os_name} ({socket.getfqdn()})</p>\n')
        self.write(f'<p><b>CPU load:</b> {percent(cpu_load)}<br>\n')
        self.write(f'{cpu} {cores}</p>\n')
        self.write(f'<p><b>GPU load compute:</b> {percent(gpu_load_compute)}')
        self.write(f' &mdash; <b>RAM usage:</b> {percent(gpu_ram_usage)}<br>\n')
        self.write(f'{gpu}</p>\n')
        self.write(f'<p><b>RAM:</b> {ram}</p>\n')
        if 'allowedRepositories' in config:
            self.write('<table class="bordered"><thead><tr><th>Allowed Repositories</th></thead>\n')
            for repository in config['allowedRepositories']:
                self.write(f'<tr><td><a href="{repository}">{repository}</a></td></tr>')
            self.write('</table>')
        if 'blockedRepositories' in config:
            self.write('<table class="bordered"><thead><tr><th>Blocked Repositories</th></thead>\n')
            for repository in config['blockedRepositories']:
                self.write(f'<tr><td><a href="{repository}">{repository}</a></td></tr>')
            self.write('</table>')
        if 'notify' in config:
            self.write(f'<table class="bordered"><thead><tr><th>Share Idle Time: {config["shareIdleTime"]}%</th></thead>\n')
            for notify in config['notify']:
                slash = notify.find('/', 8)
                if slash > -1:
                    notify = notify[0:slash]
                self.write(f'<tr><td><a href="{notify}">{notify}</a></td></tr>')
            self.write('</table>')
        self.write('<br>')
        self.write('<canvas id="graph" height="400" width="1024"></canvas>\n')
        self.write('<script src="https://cyberbotics.com/harry-plotter/0.9f/harry.min.js"></script>\n')
        self.write('<script>\n')
        self.write('window.onload = function() {\n')

        def appendData(label):
            global snapshots
            d = f"{{title:'{label}',values:["
            for s in snapshots:
                d += f'{s.data[label]},'
            return f'{d[:-1]}]}},'

        datas = ''
        datas += appendData('Webots running')
        datas += appendData('Webots idle')
        datas += appendData('CPU load')
        datas += appendData('CPU memory')
        datas += appendData('GPU load compute')
        datas += appendData('GPU load memory')
        datas += appendData('GPU memory')
        datas += appendData('Swap')
        datas += appendData('Disk')
        datas += appendData('Network sent')
        datas += appendData('Network received')

        datas = datas[:-1]  # remove the last coma
        self.write('  plotter({\n')
        self.write("    canvas: 'graph',\n")
        self.write(f'    datas:[{datas}],\n')
        self.write("""
     labels:{
        ypos:"left",
        x:100,
        y:[50,100],
        marks:2
     },
     fill:"none",
     opacity:0.5,
     linewidth:3,
     background:"#fff",
     autoscale:"top",
     grid:{
        x:[0,100]
     },
     mouseover:{
        radius:4,
        linewidth:2,
        bullet:"#444",
        shadowbox:"1,1,0,#000",
        axis:"x"
     }
  });""")
        self.write('}\n')
        self.write('</script>\n')
        self.write('</body></html>')


def update_snapshot():
    """Compute a monitoring snapshot."""
    global current_load
    global network_sent
    global network_received
    global cpu_load
    global gpu_load_compute
    global gpu_ram_usage
    global gpu_load_memory
    memory = psutil.virtual_memory()
    swap = psutil.swap_memory()
    disk = psutil.disk_usage('/')
    n = psutil.net_io_counters()
    new_network_sent = n.bytes_sent
    new_network_received = n.bytes_recv
    network_sent_rate = float(new_network_sent - network_sent) / (SNAPSHOT_REFRESH * 1000000)  # expressed in MB/s
    network_received_rate = float(new_network_received - network_received) / (SNAPSHOT_REFRESH * 1000000)  # MB/s
    network_sent = new_network_sent
    network_received = new_network_received
    global nvidia
    if nvidia:
        nvmlHandle = nvmlDeviceGetHandleByIndex(0)
        gpu_memory = nvmlDeviceGetMemoryInfo(nvmlHandle)
        gpu_ram_usage = round(100 * float(gpu_memory.used) / float(gpu_memory.total), 1)
    else:  # not supported
        nvmlHandle = 0
        gpu_ram_usage = 0
    cpu_load = psutil.cpu_percent()
    try:
        gpu_load = nvmlDeviceGetUtilizationRates(nvmlHandle)
        gpu_load_compute = gpu_load.gpu
        gpu_load_memory = gpu_load.memory
    except NVMLError:  # not supported on some hardware
        gpu_load_compute = 0
        gpu_load_memory = 0
    webots_idle = 0
    webots_running = 0
    for client in ClientWebSocketHandler.clients:
        if client.idle:
            webots_idle = webots_idle + 1
        else:
            webots_running = webots_running + 1
    snapshot = Snapshot()
    snapshot.data['Timestamp'] = int(time.time())
    snapshot.data['Webots running'] = webots_running
    snapshot.data['Webots idle'] = webots_idle
    snapshot.data['CPU load'] = cpu_load
    snapshot.data['CPU memory'] = memory.percent
    snapshot.data['GPU load compute'] = gpu_load_compute
    snapshot.data['GPU load memory'] = gpu_load_memory
    snapshot.data['GPU memory'] = gpu_ram_usage
    snapshot.data['Swap'] = swap.percent
    snapshot.data['Disk'] = disk.percent
    snapshot.data['Network sent'] = network_sent_rate
    snapshot.data['Network received'] = network_received_rate
    snapshot.write()
    # current_load reflects the maximum of CPU/swap + RAM + VRAM/GPU usage in percent
    current_load = max(cpu_load, (swap.percent + memory.percent + gpu_load_memory) / 3, gpu_load_compute)
    previous_loads.append(current_load)
    if len(previous_loads) > 5:
        del previous_loads[0]
    snapshots.append(snapshot)
    if len(snapshots) > 600:  # display data for the last 10 minutes
        del snapshots[0]
    tornado.ioloop.IOLoop.current().add_timeout(int(time.time()) + SNAPSHOT_REFRESH, update_snapshot)


def main():
    """Start the simulation server."""
    # the following config variables read from the config.json file
    # are described here:
    #
    # server:              fully qualified domain name of simulation server
    # ssl:                 for https/wss URL (true by default)
    # port:                local port on which the server is listening
    # portRewrite:         port rewritten in the URL by apache (true by default)
    # docker:              launch webots inside a docker (false by default)
    # allowedRepositories: list of allowed GitHub simulation repositories
    # blockedRepositories: list of blocked GitHub simulation repositories
    # shareIdleTime:       maximum load for running non-allowed repositories (50% by default)
    # notify:              webservices to be notified about the server status (https://webots.cloud by default)
    # projectsDir:         directory in which projects are located
    # webotsHome:          directory in which Webots is installed (WEBOTS_HOME)
    # maxConnections:      maximum number of simultaneous Webots instances
    # logDir:              directory where the log files are written
    # monitorLogEnabled:   store monitor data in a file (true by default)
    # debug:               output debug information to stdout (false by default)
    # projectCacheDir:     directory in which projects are cached
    #
    global config
    global snapshots
    global previous_loads
    global nvidia
    global network_sent
    global network_received
    global monitorFile
    n = psutil.net_io_counters()
    network_sent = n.bytes_sent
    network_received = n.bytes_recv
    snapshots = []
    previous_loads = []
    if 'docker' not in config:
        config['docker'] = False
    if config['docker']:
        if 'SSH_CONNECTION' not in os.environ:
            os.system('xhost +local:root')
        os.environ['DOCKER_BUILDKIT'] = '0'  # buildkit is enabled by default in docker compose V2
    if 'webotsHome' not in config:
        config['webotsHome'] = os.getenv('WEBOTS_HOME', '/usr/bin/webots').replace('\\', '/')
    if config['docker']:
        config['webots'] = '/usr/local/webots/webots'
        config['projectsDir'] = '/usr/local/webots-project'
    else:
        config['webots'] = config['webotsHome']
        if sys.platform == 'darwin':
            config['webots'] += '/Contents/MacOS/webots'
        elif sys.platform == 'win32':
            config['webots'] += '/msys64/mingw64/bin/webots.exe'
        else:  # linux
            config['webots'] += '/webots'
        if 'projectsDir' not in config:
            config['projectsDir'] = config['webotsHome'] + '/projects/samples/robotbenchmark'
        else:
            config['projectsDir'] = expand_path(config['projectsDir'])
    if 'port' not in config:
        config['port'] = 2000
    if 'maxConnections' not in config:
        config['maxConnections'] = 100
    if 'debug' not in config:
        config['debug'] = False
    # Minimum timeout is 6 minutes as the warning message appears on the client five minutes before the shutdown.
    # Note that the countdown begin when the subprocess is launched, not when Webots is ready.
    if 'timeout' not in config or config['timeout'] < 360:
        config['timeout'] = 7200

    if 'projectCacheDir' not in config or config['projectCacheDir'] == '':
        config['projectCacheDir'] = os.getcwd() + '/projectsCache'
    config['instancesPath'] = tempfile.gettempdir().replace('\\', '/') + '/webots/instances/'
    # create the instances path
    if os.path.exists(config['instancesPath']):
        shutil.rmtree(config['instancesPath'])
    mkdir_p(config['instancesPath'])

    # logging system
    log_formatter = logging.Formatter('%(asctime)-15s [%(levelname)-7s]  %(message)s')
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    config['logDir'] = 'log' if 'logDir' not in config else expand_path(config['logDir'])
    if not os.path.isabs(config['logDir']):
        config['logDir'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), config['logDir'])
    if not os.path.isabs(config['projectCacheDir']):
        config['projectCacheDir'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), config['projectCacheDir'])
    simulationLogDir = os.path.join(config['logDir'], 'simulation')
    logFile = os.path.join(simulationLogDir, 'output.log')
    try:
        if not os.path.exists(simulationLogDir):
            os.makedirs(simulationLogDir)
        file_handler = logging.StreamHandler(sys.stdout) if config['debug'] else \
            logging.handlers.RotatingFileHandler(logFile, maxBytes=500000, backupCount=10)
        file_handler.setFormatter(log_formatter)
        file_handler.setLevel(logging.INFO)
        root_logger.addHandler(file_handler)
    except (OSError, IOError) as e:
        sys.exit(f'Log file {logFile} cannot be created: {e}')
    # disable tornado.access INFO logs
    tornado_access_log = logging.getLogger('tornado.access')
    tornado_access_log.setLevel(logging.WARNING)

    # create monitor.csv used by Snapshot if needed
    if 'monitorLogEnabled' not in config:
        config['monitorLogEnabled'] = True
    if config['monitorLogEnabled']:
        monitorFile = os.path.join(simulationLogDir, 'monitor.csv')
        try:
            if not os.path.exists(simulationLogDir):
                os.makedirs(simulationLogDir)
            file = open(monitorFile, 'w')
            file.write('Timestamp, Webots running, Webots idle, CPU load, CPU memory, '
                       'GPU load compute, GPU load memory, GPU memory, Swap, Disk, Network sent, Network received\n')
            file.close()
        except (OSError, IOError) as e:
            logging.error(f'Log file {monitorFile} cannot be created: {e}')

    if 'notify' not in config:
        config['notify'] = ['https://webots.cloud/ajax/server/setup.php']
    elif isinstance(config['notify'], str):
        config['notify'] = [config['notify']]

    if 'shareIdleTime' not in config:
        config['shareIdleTime'] = 50

    if 'ssl' not in config:
        config['ssl'] = True

    if 'portRewrite' not in config:
        config['portRewrite'] = True

    if 'server' not in config:
        config['server'] = 'localhost'
        logging.error('Warning: server name not defined in configuration file.')

    url = 'https' if config['ssl'] else 'http'
    url += '://' + config['server']
    url += '/' if config['portRewrite'] else ':'
    url += str(config['port'])

    for server in config['notify']:
        allowedRepositories = ','.join(config['allowedRepositories']) if 'allowedRepositories' in config else ''
        retry = 6  # try once and retries 5 times if needed
        while retry > 0:
            error = False
            try:
                x = requests.post(server, data={'url': url,
                                                'shareIdleTime': config['shareIdleTime'],
                                                'allowedRepositories': allowedRepositories})
            except requests.exceptions.RequestException as e:
                error = f'Request exception: {e}'
            except requests.exceptions.HTTPError as e:
                error = f'HTTP error: {e}'
            except requests.exceptions.ConnectionError as e:
                error = f'Connection error: {e}'
            except requests.exceptions.Timeout as e:
                error = f'Timeout error: {e}'
            finally:
                if error:
                    retry -= 1
                    logging.warning(f'{error}\n')
                    if retry > 0:
                        logging.info(f'Retrying ({6 - retry}/5) in 5 seconds...\n')
                    else:
                        logging.error('Giving up\n')
                    time.sleep(5)
                else:
                    retry = 0
        logging.info(x.text)

    # startup the server
    logging.info(f"Running simulation server on port {config['port']}")

    handlers = []
    handlers.append((r'/monitor', MonitorHandler))
    handlers.append((r'/client', ClientWebSocketHandler))
    handlers.append((r'/load', LoadHandler))
    application = tornado.web.Application(handlers)
    http_server = tornado.httpserver.HTTPServer(application)
    http_server.listen(config['port'])
    try:
        nvmlInit()
        nvidia = True
    except NVMLError:
        nvidia = False
    update_snapshot()
    try:
        tornado.ioloop.IOLoop.current().start()
    except Exception:
        logging.info(traceback.format_exc())
        for client in ClientWebSocketHandler.clients:
            del client
    if nvidia:
        nvmlShutdown()


if sys.platform == 'win32' and sys.version_info >= (3, 8):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

if sys.platform == 'linux':
    # kill all the existing instances of Webots to avoid conflicts with web socket port
    os.system('killall -q webots-bin')

# specify the display to ensure Webots can be executed even if this script is started remotely from a ssh session
if 'DISPLAY' not in os.environ:
    os.environ['DISPLAY'] = ":0"
# ensure we are in the script directory
os.chdir(os.path.dirname(os.path.realpath(__file__)))
argc = len(sys.argv)
if argc == 1:
    config_json = 'config/simulation/local.json'
elif argc == 2:
    config_json = sys.argv[1]
else:
    sys.exit('Too many arguments.')
if not os.path.isabs(config_json):
    config_json = os.path.join(os.path.dirname(os.path.abspath(__file__)), config_json)
with open(config_json) as config_file:
    config = json.load(config_file)
if __name__ == '__main__':
    main()
