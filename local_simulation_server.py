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
import shutil
import signal
import socket
import subprocess
import sys
from os import walk

HOST = ''  # Any host can connect
PORT = int(sys.argv[1])  # Port to listen on

shared_folder = None
webots = None


def start_webots(connection):
    filenames = next(walk(shared_folder), (None, None, []))[2]
    worlds = list(filter(lambda file: file.endswith('.wbt'), filenames))

    if len(worlds) == 0:
        return -1
    if len(worlds) > 1:
        return -2
    world_file = os.path.join(shared_folder, worlds[0])

    lines = []
    if 'launch_args.txt' in filenames:
        launch_args_file = open(os.path.join(shared_folder, 'launch_args.txt'), 'r')
        lines = launch_args_file.readlines()
        for i in range(len(lines)):
            lines[i] = lines[i].strip()

    connection.sendall(b'ACK')
    subprocess.call(['/usr/bin/open', '-W', '-n', '-a', '/Applications/Webots.app', '--args', world_file, *lines])

    os.remove(world_file)
    if 'launch_args.txt' in filenames:
        os.remove(os.path.join(shared_folder, 'launch_args.txt'))

    return 1


def keyboardInterruptHandler(signal, frame):
    if shared_folder:
        if os.path.isdir(shared_folder):
            for element in os.listdir(shared_folder):
                element_path = os.path.join(shared_folder, element)
                if os.path.isfile(element_path):
                    os.remove(element_path)
                if os.path.isdir(element_path):
                    shutil.rmtree(element_path)
    exit(0)


signal.signal(signal.SIGINT, keyboardInterruptHandler)
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
    s.bind((HOST, PORT))
    s.listen()
    print(f'Waiting for connection on port {PORT}...')
    conn, addr = s.accept()
    with conn:
        print(f'Connection from {addr}')
        while True:
            data = conn.recv(1024)
            if not data:
                break
            shared_folder = data.decode('utf-8')
            success = start_webots(conn) if os.path.isdir(shared_folder) else 0
            if success == 0:
                message = f'FAIL: The shared folder \'{shared_folder}\' doesn\'t exist.'
                conn.sendall(message.encode('utf-8'))
            elif success == -1:
                message = 'FAIL: No world could be found in the shared folder.'
                conn.sendall(message.encode('utf-8'))
            elif success == -2:
                message = 'FAIL: More than one world was found in the shared folder.'
                conn.sendall(message.encode('utf-8'))
            if success == 1:
                print('Webots was executed successfully.')
            else:
                print(message, file=sys.stderr)
