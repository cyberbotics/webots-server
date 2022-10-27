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

"""Local simulation server."""

import os
import socket
import subprocess
import sys
from os import walk

HOST = ''  # Any host can connect
PORT = int(sys.argv[1])  # Port to listen on

shared_folder = None
tcp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

tcp_socket.bind((HOST, PORT))
tcp_socket.listen()
while True:
    print(f'Waiting for connection on port {PORT}...')
    connection, address = tcp_socket.accept()

    print(f'Connection from {address}')
    data = connection.recv(1024)
    cmd = data.decode('utf-8').split(' ')

    world_file = cmd[1]
    if not os.path.isfile(world_file):
        message = f'FAIL: The world file \'{world_file}\' doesn\'t exist.'
        connection.sendall(message.encode('utf-8'))
        print(message, file=sys.stderr)
        connection.close()
        continue

    webots_process = subprocess.Popen(cmd)
    connection.sendall(b'ACK')
    connection.settimeout(1)
    connection_closed = False
    while webots_process.poll() is None:
        try:
            data = connection.recv(1024)
        except socket.timeout:
            continue
        else:
            if not data:
                print('Connection was closed by the client.')
                connection.close()
                webots_process.kill()
                connection_closed = True
                break

    if connection_closed:
        connection_closed = False
        continue

    print('Webots was executed successfully.')
    closing_message = 'CLOSED'
    connection.sendall(closing_message.encode('utf-8'))
    connection.close()
