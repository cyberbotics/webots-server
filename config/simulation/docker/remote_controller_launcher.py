import importlib.util
from controller.wb import wb


class Initializer:
    def __init__(self):
        # Running wb_robot_init() before controller execution to send the logs
        # to the webots container stdout even if the controller crashes.
        wb.wb_robot_init()

    def __del__(self):
        # If the controller crashed, we maintain the connection up.
        # This is done to prevent the console from being spammed
        # with an error message loop caused by docker re-opening endlessly a bugged controller.
        while wb.wb_robot_step(0) != -1:
            pass


init = Initializer()

spec = importlib.util.spec_from_file_location('participant', 'participant.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
