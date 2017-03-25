from BaseHTTPServer import BaseHTTPRequestHandler, HTTPServer

import abc
import array
import inspect
import os
import subprocess
import threading
import time
from termcolor import cprint
import yaml

import gym
from gym import error, spaces, utils
from gym.utils import seeding

import numpy as np

import wx


###############################################
class ImageHelper:

    BLACK_PIXEL = (0, 0, 0)

    def GetPixelColor(self, image_array, x, y):
        base_pixel = (x + (y * config['SCR_W'])) * 3
        red = image_array[base_pixel + 0]
        green = image_array[base_pixel + 1]
        blue = image_array[base_pixel + 2]
        return (red, green, blue)


###############################################
### Variables & Constants                   ###
###############################################

config = yaml.safe_load(open(os.path.join(os.path.dirname(inspect.stack()[0][1]), "config.yml")))

MILLIS_50 = 50.0 / 1000.0

IMAGE_HELPER = ImageHelper()


###############################################
class Mupen64PlusEnv(gym.Env):
    __metaclass__ = abc.ABCMeta
    metadata = {'render.modes': ['human']}

    def __init__(self, rom_name):
        self.viewer = None
        self.step_count = 0
        self.running = True
        self.episode_over = False
        self.numpy_array = None
        self.pixel_array = array.array('B', [0] * (config['SCR_W'] *
                                                   config['SCR_H'] *
                                                   config['SCR_D']))
        self.controller_server, self.controller_server_thread = self._start_controller_server()
        self.xvfb_process, self.emulator_process = self._start_emulator(rom_name=rom_name)
        self._navigate_menu()

        self.observation_space = \
            spaces.Box(low=0, high=255, shape=(config['SCR_H'], config['SCR_W'], config['SCR_D']))

        self.action_space = spaces.MultiDiscrete([[-80, 80], # Joystick X-axis
                                                  [-80, 80], # Joystick Y-axis
                                                  [0, 1], # A Button
                                                  [0, 1], # B Button
                                                  [0, 1]]) # RB Button

    def _step(self, action):
        #cprint('Step %i: %s' % (self.step_count, action), 'green')
        self.controller_server.send_controls(action)
        obs = self._observe()
        self.episode_over = self._evaluate_end_state()
        reward = self._get_reward()

        self.step_count += 1
        return obs, reward, self.episode_over, {}

    def _observe(self):
        #cprint('Observe called!', 'yellow')

        if config['USE_XVFB']:
            offset_x = 0
            offset_y = 0
        else:
            offset_x = config['OFFSET_X']
            offset_y = config['OFFSET_Y']

        bmp = wx.Bitmap(config['SCR_W'], config['SCR_H'])
        wx.MemoryDC(bmp).Blit(0, 0,
                              config['SCR_W'], config['SCR_H'],
                              wx.ScreenDC(),
                              offset_x, offset_y)
        bmp.CopyToBuffer(self.pixel_array)

        self.numpy_array = np.frombuffer(self.pixel_array, dtype=np.uint8)
        self.numpy_array = \
            self.numpy_array.reshape(config['SCR_H'], config['SCR_W'], config['SCR_D'])

        return self.numpy_array

    @abc.abstractmethod
    def _navigate_menu(self):
        return

    @abc.abstractmethod
    def _get_reward(self):
        #cprint('Get Reward called!', 'yellow')
        return 0

    @abc.abstractmethod
    def _evaluate_end_state(self):
        #cprint('Evaluate End State called!', 'yellow')
        return False

    @abc.abstractmethod
    def _reset(self):
        cprint('Reset called!', 'yellow')

        return self._observe()

    def _render(self, mode='human', close=False):
        if close:
            if self.viewer is not None:
                self.viewer.close()
                self.viewer = None
            return
        img = self.numpy_array
        if mode == 'rgb_array':
            return img
        elif mode == 'human':
            from gym.envs.classic_control import rendering
            if self.viewer is None:
                self.viewer = rendering.SimpleImageViewer()
            self.viewer.imshow(img)
        

    def _close(self):
        cprint('Close called!', 'yellow')
        self.running = False
        self._kill_emulator()
        self._stop_controller_server()

    def _start_controller_server(self):
        server = ControllerHTTPServer(('', config['PORT_NUMBER']),
                                      config['ACTION_TIMEOUT'])
        server_thread = threading.Thread(target=server.serve_forever, args=())
        server_thread.daemon = True
        server_thread.start()
        print('ControllerHTTPServer started on port ', config['PORT_NUMBER'])
        return server, server_thread

    def _stop_controller_server(self):
        #cprint('Stop Controller Server called!', 'yellow')
        if self.controller_server is not None:
            self.controller_server.shutdown()

    def _start_emulator(self,
                        rom_name,
                        res_w=config['SCR_W'],
                        res_h=config['SCR_H'],
                        res_d=config['SCR_D'],
                        input_driver_path=config['INPUT_DRIVER_PATH']):

        rom_path = os.path.abspath(
            os.path.join(os.path.dirname(inspect.stack()[0][1]),
                         '../ROMs',
                         rom_name))

        if not os.path.isfile(rom_path):
            msg = "ROM not found: " + rom_path
            cprint(msg, 'red')
            raise Exception(msg)

        input_driver_path = os.path.abspath(os.path.expanduser(input_driver_path))
        if not os.path.isfile(input_driver_path):
            msg = "Input driver not found: " + input_driver_path
            cprint(msg, 'red')
            raise Exception(msg)

        cmd = [config['MUPEN_CMD'],
               "--resolution",
               "%ix%i" % (res_w, res_h),
               "--input",
               input_driver_path,
               rom_path]

        initial_disp = os.environ["DISPLAY"]

        xvfb_proc = None
        if config['USE_XVFB']:

            xvfb_cmd = [config['XVFB_CMD'],
                        config['XVFB_DISPLAY'],
                        "-screen",
                        "0",
                        "%ix%ix%i" % (res_w, res_h, res_d * 8),
                        "-fbdir",
                        config['TMP_DIR']]

            print('Starting xvfb with command: %s' % xvfb_cmd)

            xvfb_proc = subprocess.Popen(xvfb_cmd, shell=False, stderr=subprocess.STDOUT)

            time.sleep(2) # Give xvfb a couple seconds to start up

            os.environ["DISPLAY"] = config['XVFB_DISPLAY']

            cmd = [config['VGLRUN_CMD']] + cmd

        print('Starting emulator with comand: %s' % cmd)

        emulator_process = subprocess.Popen(cmd,
                                            env=os.environ.copy(),
                                            shell=False,
                                            stderr=subprocess.STDOUT)

        # Need to initialize this after the DISPLAY env var has been set
        # so it attaches to the correct X display; otherwise screenshots
        # come from the wrong place.
        wx.App()

        # Restore the DISPLAY env var
        os.environ["DISPLAY"] = initial_disp

        emu_mon = EmulatorMonitor()
        monitor_thread = threading.Thread(target=emu_mon.monitor_emulator,
                                          args=[emulator_process])
        monitor_thread.daemon = True
        monitor_thread.start()

        return xvfb_proc, emulator_process

    def _kill_emulator(self):
        #cprint('Kill Emulator called!', 'yellow')
        try:
            self.controller_server.send_controls(ControllerHTTPServer.NOOP)
            if self.emulator_process is not None:
                self.emulator_process.kill()
            if self.xvfb_process is not None:
                self.xvfb_process.kill()
        except AttributeError:
            pass # We may be shut down during intialization before these attributes have been set


###############################################
class EmulatorMonitor:
    def monitor_emulator(self, emulator):
        emu_return = emulator.poll()
        while emu_return is None:
            time.sleep(2)
            emu_return = emulator.poll()

        # TODO: this means our environment died... need to die too
        print('Emulator closed with code: ' + str(emu_return))


###############################################
class ControllerHTTPServer(HTTPServer, object):

    # Buttons
    NOOP = [0, 0, 0, 0, 0]
    A_BUTTON = [0, 0, 1, 0, 0]
    B_BUTTON = [0, 0, 0, 1, 0]
    RB_BUTTON = [0, 0, 0, 0, 1]
    JOYSTICK_UP = [0, 80, 0, 0, 0]
    JOYSTICK_DOWN = [0, -80, 0, 0, 0]
    JOYSTICK_LEFT = [-80, 0, 0, 0, 0]
    JOYSTICK_RIGHT = [80, 0, 0, 0, 0]

    def __init__(self, server_address, control_timeout):
        self.control_timeout = control_timeout
        self.controls = ControllerHTTPServer.NOOP
        self.hold_response = True
        self.running = True
        super(ControllerHTTPServer, self).__init__(server_address, self.ControllerRequestHandler)

    def send_controls(self, controls):
        #print('Send controls called')
        self.controls = controls
        self.hold_response = False

        # Wait for controls to be sent:
        start = time.time()
        while not self.hold_response and time.time() < start + self.control_timeout:
            time.sleep(MILLIS_50)

    def shutdown(self):
        self.running = False
        super(ControllerHTTPServer, self).shutdown()


    class ControllerRequestHandler(BaseHTTPRequestHandler, object):

        def log_message(self, format, *args):
            pass

        def write_response(self, resp_code, resp_data):
            self.send_response(resp_code)
            self.send_header("Content-type", "text/plain")
            self.end_headers()
            self.wfile.write(resp_data)

        def do_GET(self):

            while self.server.running and self.server.hold_response:
                time.sleep(MILLIS_50)

            if not self.server.running:
                print('Sending SHUTDOWN response')
                # TODO: This sometimes fails with a broken pipe because
                # the emulator has already stopped. Should handle gracefully
                self.write_response(500, "SHUTDOWN")

            ### respond with controller output
            self.write_response(200, self.server.controls)

            self.server.hold_response = True
            return

###############################################
