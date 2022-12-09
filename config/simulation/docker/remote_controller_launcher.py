import importlib.util
import os
from controller.wb import wb


class Initializer:
    def __init__(self):
        # Running wb_robot_init() before controller execution to send the logs to webots container stdout even if controller crashes.
        wb.wb_robot_init()

    def __del__(self):
        # If the controller crashed, we maintain the connection up.
        # This is done to prevent the console from being spammed with error loop from docker re-opening endlessly a bugged controller.
        while wb.wb_robot_step(0) != -1:
            pass


init = Initializer()

default_controller = os.environ['DEFAULT_CONTROLLER']
spec = importlib.util.spec_from_file_location(default_controller, f'{default_controller}.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
