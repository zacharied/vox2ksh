from enum import Enum, auto
from typing import Union

from recordclass import dataobject
from xml.etree import ElementTree
from shutil import copyfile
import traceback
import random
import math

import sys, os
import argparse

from os.path import splitext as splitx
from os.path import join as pjoin

# Ticks per a beat of /4 time
TICKS_PER_BEAT = 48

SLAM_TICKS = 4

FX_CHIP_SOUND_COUNT = 14

AUDIO_EXTENSION = '.ogg'
FX_CHIP_SOUND_EXTENSION = '.wav'
VOX_ROOT = 'data'

class Debug:
    class State(Enum):
        INPUT = auto()
        OUTPUT = auto()

    class Level(Enum):
        ABNORMALITY = 'abnormal'
        WARNING = 'warning'
        ERROR = 'error'

    def __init__(self, exceptions_file="exceptions.txt"):
        self.state = None
        self.input_filename = None
        self.output_filename = None
        self.current_line_num = None
        self.exceptions_count = {}
        self._exceptions_file = open(exceptions_file, "w+")

    def reset(self):
        for level in self.Level:
            self.exceptions_count[level] = 0

    def close(self):
        self._exceptions_file.close()

    def current_filename(self):
        return self.input_filename if self.state == self.State.INPUT else self.output_filename

    def record(self, level, tag, message):
        self.exceptions_count[level] += 1
        print(f'{self.current_filename()}:{self.current_line_num}\n{level.value} / {tag}: {message}\n', file=self._exceptions_file)

    def record_last_exception(self, level=Level.WARNING, tag='python_exception', trace=False):
        self.record(level, tag, traceback.format_exc() if trace else sys.exc_info()[1])

def truncate(x, digits) -> float:
    stepper = 10.0 ** digits
    return math.trunc(stepper * x) / stepper

class VoxLoadError(Exception):
    pass

class VoxParseError(Exception):
    def __init__(self, section, msg):
        self.section = section
        self.msg = msg

    def __str__(self):
        return f'({self.section}) {self.msg}'

class KshConvertError(Exception):
    pass

class TimeSignature:
    def __init__(self, top, bottom):
        self.top: int = top
        self.bottom: int = bottom

    def ticks_per_beat(self):
        return int(TICKS_PER_BEAT * (4 / self.bottom))

class Timing:
    # TODO Take timesig as a param
    def __init__(self, measure, beat, offset):
        self.measure: int = measure
        self.beat: int = beat
        self.offset: int = offset

    @classmethod
    def from_time_str(cls, time: str):
        """ Create a Timing from the format string that appears in the first column of vox tracks. """
        splitted = time.split(',')
        # TODO Use current time signature
        if int(splitted[2]) >= TICKS_PER_BEAT:
            raise ValueError(f'offset of {int(splitted[2])} is greater than maximum')
        return cls(int(splitted[0]), int(splitted[1]), int(splitted[2]))

    def diff(self, other, timesig):
        return (self.measure - other.measure) * (timesig.ticks_per_beat() * timesig.top) \
               + (self.beat - other.beat) * timesig.ticks_per_beat() \
               + (self.offset - other.offset)

    def add(self, ticks, timesig):
        new = Timing(self.measure, self.beat, self.offset + ticks)
        while new.offset >= timesig.ticks_per_beat():
            new.offset -= timesig.ticks_per_beat()
            new.beat += 1
        while new.beat > timesig.top:
            new.beat -= timesig.top
            new.measure += 1
        return new

    def __eq__(self, other):
        return self.measure == other.measure and self.beat == other.beat and self.offset == other.offset

    def __hash__(self):
        return hash((self.measure, self.beat, self.offset))

    def __str__(self):
        return '{},{},{}'.format(self.measure, self.beat, self.offset)

    def __cmp__(self, other):
        # TODO There has to be a better way to express this
        if self.measure == other.measure:
            if self.beat == other.beat:
                return self.offset - other.offset
            return self.beat - other.beat
        return self.measure - other.measure

class CameraNode(dataobject):
    start_param: float
    end_param: float
    duration: int

# TODO Use mapped_enum
class SpcParam(Enum):
    """ SPCONTROLLER section param. """
    @classmethod
    def from_vox_name(cls, vox_name):
        if vox_name == 'CAM_RotX':
            return cls.ROT_X
        elif vox_name == 'CAM_Radi':
            return cls.RAD_I
        elif vox_name == 'Realize':
            return cls.REALIZE
        elif vox_name == 'AIRL_ScaX':
            return cls.AIRL_SCAX
        elif vox_name == 'AIRR_ScaX':
            return cls.AIRR_SCAX
        elif vox_name == 'Tilt':
            return cls.TILT

        raise ValueError(f'invalid camera param "{vox_name}"')

    def to_ksh_name(self):
        if self == self.ROT_X:
            return 'zoom_top'
        elif self == self.RAD_I:
            return 'zoom_bottom'
        elif self == self.TILT:
            return 'tilt'
        else:
            return None

    def to_ksh_value(self, val:float=0):
        # Convert the vox value to the one that will be printed to the ksh.
        if self == self.ROT_X:
            return int(val * 150.0)
        elif self == self.RAD_I:
            return int(val * -150.0)
        elif self == self.TILT:
            return truncate(val * -1.0, 1)
        return None

    ROT_X = auto()
    RAD_I = auto()
    REALIZE = auto()
    AIRL_SCAX = auto()
    AIRR_SCAX = auto()
    TILT = auto()

    @classmethod
    def line_is_abnormal(cls, param, splitted):
        cell = lambda i: splitted[i].strip()
        if param == SpcParam.REALIZE:
            if cell(2) == '3':
                return cell(4) != '36.12' or cell(5) != '60.12' or cell(6) != '110.12' or cell(7) != '0.00'
            elif cell(2) == '4':
                return cell(4) != '0.62' or cell(5) != '0.72' or cell(6) != '1.03' or cell(7) != '0.00'
        return False
        # TODO Other params maybe

class KshFilter(Enum):
    @classmethod
    def from_vox_filter_id(cls, filter_id):
        if filter_id == 0:
            return cls.PEAK
        elif filter_id == 1 or filter_id == 2:
            return cls.LOWPASS
        elif filter_id == 3 or filter_id == 4:
            return cls.HIGHPASS
        elif filter_id == 5:
            return cls.BITCRUSH
        raise ValueError(f'unrecognized vox filter id {filter_id}')

    def to_ksh_name(self):
        if self == self.PEAK:
            return 'peak'
        elif self == self.LOWPASS:
            return 'lpf1'
        elif self == self.HIGHPASS:
            return 'hpf1'
        elif self == self.BITCRUSH:
            return 'bitc'

    PEAK = auto()
    LOWPASS = auto()
    HIGHPASS = auto()
    BITCRUSH = auto()

# TODO Use mapped_enum
class KshEffect(Enum):
    def to_ksh_simple_name(self):
        return self.value

    @classmethod
    def choose_random(cls):
        choice = random.choice(cls)
        if choice == cls.TAPESTOP or choice == cls.PITCHSHIFT:
            return cls.choose_random()
        return choice

    RETRIGGER = 'Retrigger'
    GATE = 'Gate'
    FLANGER = 'Flanger'
    BITCRUSHER = 'BitCrusher'
    PHASER = 'Phaser'
    WOBBLE = 'Wobble'
    PITCHSHIFT = 'PitchShift'
    TAPESTOP = 'TapeStop'
    ECHO = 'Echo'
    SIDECHAIN = 'SideChain'

class KshEffectDefine:
    def __init__(self):
        self.effect = None
        self.main_param = None
        self.params = {}

    def to_define_line(self, index):
        param_str = ''
        for k, v in self.params.items():
            param_str += f';{k}={v}'
        return f'#define_fx {index} type={self.effect.to_ksh_simple_name()}{param_str}'

    def fx_change(self, index):
        extra = f';{self.main_param}' if self.main_param is not None else ''
        return f'{index}{extra}'

    @classmethod
    def from_pre_v4_vox_sound_id(cls, sound_id):
        """Generate an effect definition line from the old-style effect declaration."""
        define = cls.default_effect()

        if sound_id == 2:
            define.effect = KshEffect.RETRIGGER
            define.main_param = '8'
        elif sound_id == 3:
            define.effect = KshEffect.RETRIGGER
            define.main_param = '16'
        elif sound_id == 4:
            define.effect = KshEffect.GATE
            define.main_param = '16'
        elif sound_id == 5:
            define.effect = KshEffect.FLANGER # TODO Tweak
            define.main_param = '200' # TODO Screw you USC (by default, flangers have no effect)
        elif sound_id == 6:
            define.effect = KshEffect.RETRIGGER
            define.main_param = '32'
        elif sound_id == 7:
            define.effect = KshEffect.GATE
            define.main_param = '8'
        elif sound_id == 8:
            define.effect = KshEffect.PITCHSHIFT
            define.main_param = '8' # TODO Tweak
        elif sound_id > 8:
            raise ValueError(f'old vox sound id {sound_id} does not exist')

        return define

    @classmethod
    def default_effect(cls):
        define = KshEffectDefine()
        define.effect = KshEffect.FLANGER
        return define

    @classmethod
    def from_effect_info_line(cls, line):
        splitted = line.replace('\t', '').split(',')

        define = KshEffectDefine()
        define.effect = KshEffect.FLANGER

        if splitted[0] == '1' or splitted[0] == '8':
            # TODO No way this is right
            # Retrigger / echo (they're pretty much the same thing)
            define.effect = KshEffect.RETRIGGER

            if float(splitted[3]) < 0:
                define.main_param = int(float(splitted[1]) * 16)
                define.params['waveLength'] = f"1/{define.main_param}"
                define.params['updatePeriod'] = "1/18"
            else:
                define.main_param = int((4 / float(splitted[3])) * float(splitted[1]))
                define.params['waveLength'] = f'1/{define.main_param}'
                define.params['updatePeriod'] = f"1/{int(4 / float(splitted[3]))}"
            rate = f'{int(float(splitted[5]) * 100)}%'
            feedback_level = f'{int(float(splitted[4]) * 100)}%'

            define.params['mix'] = f'0%>{int(float(splitted[2]))}%'

            if feedback_level != '100%':
                define.effect = KshEffect.ECHO
                define.params['feedbackLevel'] = feedback_level
                if splitted[0] == '8':
                    define.params['updatePeriod'] = 0
                define.params['updateTrigger'] = 'off>on' if splitted[0] == '8' else 'off'
                define.main_param = f'{define.main_param};{feedback_level}'
            elif float(splitted[3]) < 0:
                define.params['rate'] = rate
            else:
                define.params['rate'] = rate
                if splitted[0] == '8':
                    define.params['updateTrigger'] = 'off>on'
                    define.params['updatePeriod'] = 0

        elif splitted[0] == '2':
            # This is probably correct
            # Gate
            define.effect = KshEffect.GATE
            define.main_param = int((2 / float(splitted[3])) * float(splitted[2]))
            define.params['waveLength'] = f'1/{define.main_param}'
            define.params['mix'] = f'0%>{int(float(splitted[1]))}%'

        elif splitted[0] == '3':
            # TODO Figure this out
            # Phaser (more like chorus)
            define.effect = KshEffect.PHASER
            define.main_param = '1'
            define.params['stereoWidth'] = f'{int(float(splitted[4]))}%'
            define.params['Q'] = str(float(splitted[3]))
            define.params['mix'] = f'0%>{int(float(splitted[1]))}%'
            define.params['period'] = define.main_param

        elif splitted[0] == '4':
            # TODO This needs some tweaking
            # Tape stop
            define.effect = KshEffect.TAPESTOP
            define.params['mix'] = f'0%>{int(float(splitted[1]))}%'
            speed = float(splitted[3]) * float(splitted[2]) * 9.8125
            if speed > 50:
                speed = 50
            else:
                speed = int(speed)
            define.main_param = speed
            define.params['speed'] = f'{define.main_param}%'

        elif splitted[0] == '5':
            # TODO Investigate if this is right
            # Sidechain
            define.effect = KshEffect.SIDECHAIN
            define.main_param = int(float(splitted[2]) * 2)
            define.params['period'] = f'1/{define.main_param}'

        elif splitted[0] == '6':
            # Wobble
            define.effect = KshEffect.WOBBLE
            define.main_param = int(float(splitted[6])) * 4
            define.params['waveLength'] = f'1/{define.main_param}'
            define.params['loFreq'] = f'{int(float(splitted[4]))}Hz'
            define.params['hiFreq'] = f'{int(float(splitted[5]))}Hz'
            define.params['Q'] = float(splitted[7])
            define.params['mix'] = f'0%>{int(float(splitted[3]))}%'

        elif splitted[0] == '7':
            # Bitcrusher
            define.effect = KshEffect.BITCRUSHER
            define.main_param = int(splitted[2])
            define.params['reduction'] = f'{define.main_param}samples'
            define.params['mix'] = f'0%>{int(float(splitted[1]))}%'

        elif splitted[0] == '9':
            # Pitchshift
            define.effect = KshEffect.PITCHSHIFT
            define.main_param = int(float(splitted[2]))
            define.params['pitch'] = define.main_param
            define.params['mix'] = f'0%>{int(float(splitted[1]))}'

        elif splitted[0] == '11':
            define.effect = KshEffect.WOBBLE
            define.main_param = 1
            define.params['loFreq'] = f'{int(float(splitted[3]))}Hz'
            define.params['hiFreq'] = define.params["loFreq"]
            define.params['Q'] = '1.4'

        elif splitted[0] == '12':
            # High pass effect
            # TODO This is not right at all and is a placeholder
            define.effect = KshEffect.FLANGER
            define.params['depth'] = '200samples'
            define.params['volume'] = '100%'
            define.params['mix'] = f'0%>100%'

        else:
            raise ValueError(f'effect define id {splitted[0]} is not supported')

        return define

class TabEffectInfo:
    # TODO This is a placeholder
    @staticmethod
    def line_is_abnormal(line_num, line):
        line = line.strip()
        if line_num == 1:
            return line != '1,	90.00,	400.00,	18000.00,	0.70'
        elif line_num == 2:
            return line != '1,	90.00,	600.00,	15000.00,	5.00'
        elif line_num == 3:
            return line != '2,	90.00,	40.00,	5000.00,	0.70'
        elif line_num == 4:
            return line != '2,	90.00,	40.00,	2000.00,	3.00'
        elif line_num == 5:
            return line != '3,	100.00,	30'
        raise ValueError(f'invalid line number {line_num}')

class TabParamAssignInfo:
    # TODO This is a placeholder.
    @staticmethod
    def line_is_abnormal(line):
        return not line.endswith('0,	0.00,	0.00')

class Button(Enum):
    def is_fx(self):
        return self == Button.FX_L or self == Button.FX_R

    @classmethod
    def from_track_num(cls, num: int):
        try:
            return next(x for x in cls if x.value == num)
        except StopIteration as err:
            if num != 9:
                raise ValueError(f'invalid track number for button: {num}') from err

    def to_track_num(self):
        return self.value

    BT_A = 3
    BT_B = 4
    BT_C = 5
    BT_D = 6
    FX_L = 2
    FX_R = 7

class ButtonPress(dataobject):
    button: Button
    duration: int
    effect: int

class LaserSide(Enum):
    LEFT = 'l', 1
    RIGHT = 'r', 8

    def to_letter(self):
        return self.value[0]

    def to_track_num(self):
        return self.value[1]

class LaserCont(Enum):
    """ The continuity status of a laser node. """
    CONTINUE = 0
    START = 1
    END = 2

class RollKind(Enum):
    MEASURE = 1
    HALF_MEASURE = 2
    THREE_BEAT = 3
    CANCER = 4
    SWING = 5

class LaserNode:
    class Builder(dataobject):
        side: LaserSide = None
        position: int = None
        node_type: LaserCont = None
        range: int = 1
        filter: KshFilter = KshFilter.PEAK
        roll_kind: RollKind = None

    def __init__(self, builder: Builder):
        self.side: LaserSide = builder.side
        self.position: int = builder.position
        self.node_cont: LaserCont = builder.node_type
        self.range: int = builder.range
        self.filter: KshFilter = builder.filter
        self.roll_kind: RollKind = builder.roll_kind

        if self.position < 0 or self.position > 127:
            raise ValueError(f'position {self.position} is out of bounds')

    def position_ksh(self):
        """ Convert the position from the 7-bit scale to whatever the hell KSM is using. """
        # TODO This is wrong occassionally (try lowermost revolt 16)
        chars = []
        for char in range(10):
            chars.append(chr(ord('0') + char))
        for char in range(24):
            chars.append(chr(ord('A') + char))
        for char in range(15):
            chars.append(chr(ord('a') + char))
        idx = math.floor((self.position / 127) * (len(chars) - 1))
        return chars[idx]

class LaserSlam:
    class Direction(Enum):
        LEFT = auto()
        RIGHT = auto()

    def __init__(self, start: LaserNode, end: LaserNode):
        self.start: LaserNode = start
        self.end: LaserNode = end

        if self.start.position == self.end.position:
            raise ValueError('attempt to create a slam with the same start and end')
        elif self.start.side != self.end.side:
            raise ValueError('attempt to create a slam with start and end having different sides')

    def direction(self):
        if self.start.position > self.end.position:
            return self.Direction.LEFT
        else:
            return self.Direction.RIGHT

    def side(self):
        return self.start.side

class Difficulty(Enum):
    NOVICE = 0, 'n', 'novice'
    ADVANCED = 1, 'a', 'challenge'
    EXHAUST = 2, 'e', 'extended'
    INFINITE = 3, 'i', 'infinite'
    MAXIMUM = 4, 'm', 'infinite'

    @classmethod
    def from_letter(cls, k):
        return next(x for x in cls if x.value[1] == k)

    def to_letter(self):
        return self.value[1]

    @classmethod
    def from_number(cls, num):
        try:
            return next(x for x in cls if x.value[0] == num)
        except StopIteration:
            # TODO Error handling.
            return None

    def to_ksh_name(self):
        return self.value[2]

    def to_xml_name(self):
        return self.name.lower()

    def to_jacket_ifs_numer(self):
        return self.value[0] + 1

class InfiniteVersion(Enum):
    INFINITE = 2
    GRAVITY = 3
    HEAVENLY = 4
    VIVID = 5

class TiltMode(Enum):
    NORMAL = auto()
    BIGGER = auto()
    KEEP_BIGGER = auto()

    @classmethod
    def from_vox_id(cls, vox_id):
        if vox_id == 0:
            return cls.NORMAL
        elif vox_id == 1:
            return cls.BIGGER
        elif vox_id == 2:
            return cls.KEEP_BIGGER
        return None

    def to_ksh_name(self):
        if self == self.NORMAL:
            return 'normal'
        elif self == self.BIGGER:
            return 'bigger'
        elif self == self.KEEP_BIGGER:
            return 'keep_bigger'
        return None

class EventKind(Enum):
    TRACK = auto()
    TIMESIG = auto()
    BPM = auto()
    TILTMODE = auto()
    SPCONTROLLER = auto()

class KshLineBuf:
    """ Represents a single line of notes in KSH (along with effect assignment lines) """
    class ButtonState(Enum):
        NONE = auto()
        PRESS = auto()
        HOLD = auto()

    def __init__(self):
        self.buttons = {}
        self.lasers = {}
        self.spin = ''
        self.meta = []

        for bt in Button:
            self.buttons[bt] = self.ButtonState.NONE

        for side in LaserSide:
            self.lasers[side] = '-'

    def out(self):
        buf = ''

        for m in self.meta:
            buf += m + '\n'

        for bt in [ Button.BT_A, Button.BT_B, Button.BT_C, Button.BT_D ]:
            if self.buttons[bt] == self.ButtonState.HOLD:
                buf += '2'
            elif self.buttons[bt] == self.ButtonState.PRESS:
                buf += '1'
            else:
                buf += '0'

        buf += '|'

        for bt in [ Button.FX_L, Button.FX_R ]:
            if self.buttons[bt] == self.ButtonState.HOLD:
                buf += '1'
            elif self.buttons[bt] == self.ButtonState.PRESS:
                buf += '2'
            else:
                buf += '0'

        buf += '|'

        for laser in [ LaserSide.LEFT, LaserSide.RIGHT ]:
            buf += self.lasers[laser]

        buf += self.spin

        return buf

class Vox:
    class State(Enum):
        @classmethod
        def from_token(cls, token):
            if token.startswith('=') or token.startswith(' '):
                return None
            elif token == 'END':
                return cls.NONE
            if token == 'FORMAT VERSION':
                return cls.FORMAT_VERSION
            elif token == 'BPM':
                return cls.BPM
            elif token == 'BPM INFO':
                return cls.BPM_INFO
            elif token == 'TILT MODE INFO':
                return cls.TILT_INFO
            elif token == 'BEAT INFO':
                return cls.BEAT_INFO
            elif token == 'END POSISION' or token == 'END POSITION':
                return cls.END_POSITION
            elif token == 'SOUND ID START':
                return cls.SOUND_ID
            elif token == 'FXBUTTON EFFECT INFO':
                return cls.FXBUTTON_EFFECT
            elif token == 'SPCONTROLER' or token == 'SPCONTROLLER':
                return cls.SPCONTROLLER
            elif token == 'TAB EFFECT INFO':
                return cls.TAB_EFFECT
            elif token == 'TAB PARAM ASSIGN INFO':
                return cls.TAB_PARAM_ASSIGN
            elif token == 'TRACK AUTO TAB':
                return cls.AUTO_TAB
            elif token.startswith('TRACK'):
                return cls.TRACK, int(token[5])

        NONE = auto()
        FORMAT_VERSION = auto()
        BPM = auto()
        BPM_INFO = auto()
        TILT_INFO = auto()
        BEAT_INFO = auto()
        END_POSITION = auto()
        SOUND_ID = auto()
        TAB_EFFECT = auto()
        FXBUTTON_EFFECT = auto()
        TAB_PARAM_ASSIGN = auto()
        TRACK = auto()
        AUTO_TAB = auto()
        SPCONTROLLER = auto()

    def __init__(self):
        self.voxfile = None
        self.ascii = None
        self.game_id = 0
        self.song_id = 0
        self.vox_version = 0
        self.vox_defines = {} # defined in the vox file
        self.effect_defines = {} # will be defined in the ksh file
        self.end = None
        self.events = {}

        self.last_time = Timing(1, 1, 0)
        self.new_laser = False

        self.state = None
        self.state_track = 0

        self.metadata: ElementTree = None
        self.difficulty = None
        self.difficulty_idx = 0

        self.finalized = False

        self.required_chip_sounds = set()

    def __str__(self):
        return f'{self.ascii} {self.diff_token()}'

    def diff_token(self):
        return str(self.difficulty_idx) + self.difficulty.to_letter()

    def get_metadata(self, tag, from_diff=False):
        if from_diff:
            the_diff = None
            for diff in self.metadata.find('difficulty').iter():
                if diff.tag == self.difficulty.to_xml_name():
                    the_diff = diff
                    break
            if the_diff is None:
                raise LookupError(f'difficulty {self.difficulty.to_xml_name()} not found in the "music" element')
            metadata = the_diff.find(tag).text.translate(METADATA_FIX)
        else:
            elem = self.metadata.find('info')
            if elem is not None:
                metadata = self.metadata.find('info').find(tag).text.translate(METADATA_FIX)
            else:
                raise LookupError('no metadata found')
        for p in METADATA_FIX:
            metadata = metadata.replace(p[0], p[1])
        return metadata

    def bpm_string(self):
        if self.get_metadata('bpm_min') == self.get_metadata('bpm_max'):
            return int(int(self.get_metadata('bpm_min')) / 100)
        else:
            return f'{int(int(self.get_metadata("bpm_min")) / 100)}-{int(int(self.get_metadata("bpm_max")) / 100)}'

    def timing_point(self, timing):
        if timing not in self.events:
            self.events[timing] = {}

    def get_real_difficulty(self) -> str:
        if self.difficulty == Difficulty.INFINITE:
            return next(iter([v for v in InfiniteVersion if v.value == int(self.get_metadata('inf_ver'))]))
        return self.difficulty.name.lower()

    @staticmethod
    def has_action_event(event_map):
        """
        :param event_map: The associative array mapping EventKinds to their respective events
        :return: True if there is an "action" event (BT/FX press or hold start, VOL start node, slam) in the map
        """
        for kind in event_map.keys():
            if type(kind) is tuple and kind[0] == EventKind.TRACK:
                if 2 <= kind[1] <= 7 or type(event_map[kind]) is LaserSlam:
                    return True

    def time_iter(self, start: Timing, timesig: TimeSignature, condition_event=None, condition_timing=None):
        """
        Returns an iterator for the timing and event (or None) for each tick of the chart
        :param start: the Timing moment to start iterating from
        :param timesig: the timesig to start in
        :param condition_event: a function that takes one argument, the event, and returns true to stop iterating there
        :param condition_timing: a function that takes one argument, the timing, and returns true to stop iteration there
        :return: the iterator
        """
        if start.offset > timesig.ticks_per_beat():
            raise ValueError(f'start offset ({start.offset}) greater than ticks per beat ({timesig.ticks_per_beat()})')

        if condition_timing is None:
            condition_timing = lambda t: t == self.end

        last_timesig = timesig

        m = 0
        while True:
            measure = m + 1

            now = Timing(measure, 1, 0)
            if now in self.events:
                for kind, event in self.events[now].items():
                    if kind == EventKind.TIMESIG:
                        last_timesig = self.events[now][EventKind.TIMESIG]

            for b in range(0, last_timesig.top):
                beat = b + 1

                for offset in range(0, last_timesig.ticks_per_beat()):
                    now = Timing(measure, beat, offset)

                    if now in self.events and condition_event is not None:
                        if condition_event(self.events[now]):
                            return

                    if condition_timing is not None:
                        if condition_timing(now):
                            return

                    yield (now, self.events[now] if now in self.events else None)

            m += 1

    @classmethod
    def from_file(cls, path):
        parser = Vox()

        file = open(path, 'r', encoding='cp932')
        parser.voxfile = file

        filename_array = os.path.basename(path).split('_')
        with open('data/music_db.xml', encoding='cp932') as db:
            try:
                parser.game_id = int(filename_array[0])
                parser.song_id = int(filename_array[1])
                parser.difficulty = Difficulty.from_letter(os.path.splitext(path)[0][-1])
                parser.difficulty_idx = os.path.splitext(path)[0][-2]
            except ValueError:
                raise VoxLoadError(parser.voxfile.name, f'unable to parse difficulty from file name "{path}"')

            tree = ElementTree.fromstring(db.read()).findall('''.//*[@id='{}']'''.format(parser.song_id))

            if len(tree) == 0:
                raise VoxLoadError(parser.voxfile.name, f'unable to find metadata for song')

            parser.metadata = tree[0]

        parser.ascii = parser.get_metadata('ascii')

        return parser

    def parse(self):
        global debug

        line_no = 0
        section_line_no = 0

        for line in self.voxfile:
            section_line_no += 1
            line_no += 1
            debug.current_line_num = line_no

            line = line.strip()

            if line.startswith('//'):
                continue

            if line.startswith('#'):
                token_state = self.State.from_token(line.split('#')[1])
                if token_state is None:
                    continue
                if type(token_state) is tuple:
                    self.state = token_state[0]
                    self.state_track = int(token_state[1])
                else:
                    self.state = token_state
                section_line_no = 0

            elif line.startswith('define\t'):
                splitted = line.split('\t')
                if len(splitted) != 3:
                    debug.record(Debug.Level.WARNING, 'fx_define', f'define line "{line}" does not have 3 operands')
                    continue

                self.vox_defines[splitted[1]] = int(splitted[2])
                if int(splitted[2]) != 0:
                    self.effect_defines[int(splitted[2])] = KshEffectDefine.from_pre_v4_vox_sound_id(int(splitted[2]))

            elif self.state is not None:
                self.process_state(line, section_line_no)

        self.finalized = True

    def process_state(self, line, section_line_num):
        global debug

        splitted = line.split('\t')

        if line == '':
            return

        now = None
        try:
            now = Timing.from_time_str(splitted[0])
            self.timing_point(now)
        except IndexError:
            pass

        if self.state == self.State.FORMAT_VERSION:
            self.vox_version = int(line)

        elif self.state == self.State.BEAT_INFO:
            timesig = TimeSignature(int(splitted[1]), int(splitted[2]))
            self.events[now][EventKind.TIMESIG] = timesig

        elif self.state == self.State.BPM:
            now = Timing(1, 1, 0)
            self.timing_point(now)
            self.events[now][EventKind.BPM] = float(line)

        elif self.state == self.State.BPM_INFO:
            if splitted[2] != '4':
                debug.record(Debug.Level.ABNORMALITY, 'bpm_info', f'non-4 beat division in bpm info: {splitted[2]}')
            self.events[now][EventKind.BPM] = float(splitted[1])

        elif self.state == self.State.TILT_INFO:
            try:
                self.events[now][EventKind.TILTMODE] = TiltMode.from_vox_id(int(splitted[1]))
            except ValueError:
                debug.record_last_exception(level=Debug.Level.WARNING)

        elif self.state == self.State.END_POSITION:
            self.end = now

        elif self.state == self.State.SOUND_ID:
            # The `define` handler takes care of this outside of this loop.
            debug.record(Debug.Level.WARNING, 'vox_parse', f'({self.state}) line other than a #define was encountered in SOUND ID')

        elif self.state == self.State.TAB_EFFECT:
            # TODO Tab effects
            if TabEffectInfo.line_is_abnormal(section_line_num, line):
                debug.record(Debug.Level.ABNORMALITY, 'tab_effect', f'tab effect info abnormal: {line}')

        elif self.state == self.State.FXBUTTON_EFFECT:
            if self.vox_version < 6:
                # Below v6, the defines come one after another with no spacing between other than the newline.
                try:
                    self.effect_defines[section_line_num - 1] = KshEffectDefine.from_effect_info_line(line)
                except ValueError:
                    self.effect_defines[section_line_num - 1] = KshEffectDefine.default_effect()
                    debug.record_last_exception(tag='fx_load')
            else:
                if (section_line_num - 1) % 3 < 2:
                    # The < 2 condition will allow the second line to override the first.
                    if line.isspace():
                        debug.record(Debug.Level.WARNING, 'fx_load', 'fx effect info line is blank')
                    elif splitted[0] != '0,':
                        index = int(section_line_num / 3)
                        try:
                            self.effect_defines[index] = KshEffectDefine.from_effect_info_line(line)
                        except ValueError:
                            self.effect_defines[index] = KshEffectDefine.default_effect()
                            debug.record_last_exception(level=Debug.Level.WARNING, tag='fx_load')

        elif self.state == self.State.TAB_PARAM_ASSIGN:
            if TabParamAssignInfo.line_is_abnormal(line):
                debug.record(Debug.Level.ABNORMALITY, 'tab_param_assign', f'tab param assign info abnormal: {line}')

        elif self.state == self.State.SPCONTROLLER:
            try:
                param = SpcParam.from_vox_name(splitted[1])
            except ValueError:
                debug.record_last_exception(tag='spcontroller_load')
                return

            if param is not None:
                self.events[now][(EventKind.SPCONTROLLER, param)] = CameraNode(float(splitted[4]), float(splitted[5]), int(splitted[3]))
            if SpcParam.line_is_abnormal(param, splitted):
                debug.record(Debug.Level.ABNORMALITY, 'spcontroller_load', 'spcontroller line is abnormal')

        elif self.state == self.state.TRACK:
            if self.state_track == 1 or self.state_track == 8:
                laser_node = LaserNode.Builder()
                laser_node.side = LaserSide.LEFT if self.state_track == 1 else LaserSide.RIGHT
                laser_node.position = int(splitted[1])
                laser_node.node_type = LaserCont(int(splitted[2]))
                try:
                    laser_node.roll_kind = next(iter([r for r in RollKind if r.value == int(splitted[3])]))
                except StopIteration:
                    if splitted[3] != '0':
                        debug.record(Debug.Level.ABNORMALITY, 'roll_parse', f'roll type: {splitted[3]}')

                if len(splitted) > 4:
                    try:
                        laser_node.filter = KshFilter.from_vox_filter_id(int(splitted[4]))
                    except ValueError:
                        debug.record_last_exception(tag='laser_load')

                if len(splitted) > 5:
                    laser_node.range = int(splitted[5])

                laser_node = LaserNode(laser_node)

                # Check if it's a slam.
                if (EventKind.TRACK, self.state_track) in self.events[now]:
                    self.new_laser = False
                else:
                    self.new_laser = True

                if not (EventKind.TRACK, self.state_track) in self.events[now]:
                    self.events[now][(EventKind.TRACK, self.state_track)] = laser_node
                else:
                    slam_start = self.events[now][(EventKind.TRACK, self.state_track)]
                    if type(slam_start) is LaserSlam:
                        # A few charts have three laser nodes at the same time point for some reason.
                        slam_start = slam_start.end
                    slam = LaserSlam(slam_start, laser_node)
                    self.events[now][(EventKind.TRACK, self.state_track)] = slam

            else:
                try:
                    button = Button.from_track_num(self.state_track)
                except ValueError:
                    debug.record_last_exception(tag='button_load')
                    return

                if button is None:
                    # Ignore track 9 buttons.
                    return

                fx_data = None
                duration = int(splitted[1])
                if button.is_fx():
                    # Process effect assignment.
                    if duration > 0:
                        # Fx hold.
                        if self.vox_version < 4:
                            fx_data = int(splitted[3]) if splitted[3].isdigit() else int(self.vox_defines[splitted[3]])
                        else:
                            fx_data = int(splitted[2]) - 2
                    else:
                        # Fx chip, check for sound.
                        if self.vox_version >= 4:
                            sound_id = int(splitted[2])
                            if sound_id != -1 and sound_id != 255 and (sound_id >= FX_CHIP_SOUND_COUNT or sound_id < 0):
                                debug.record(Debug.Level.WARNING, 'chip_sound_parse', f'unhandled chip sound id {sound_id}')
                            elif 1 <= sound_id < FX_CHIP_SOUND_COUNT:
                                fx_data = sound_id
                                self.required_chip_sounds.add(sound_id)

                self.events[now][(EventKind.TRACK, self.state_track)] = ButtonPress(button, int(splitted[1]), fx_data)


    def write_to_ksh(self, jacket_idx=None, infinite_audio=False, infinite_preview=False, file=sys.stdout):
        global args
        global debug

        track_basename = '' if infinite_audio is None else \
            f'track{AUDIO_EXTENSION}' if not infinite_audio else f'track_inf{AUDIO_EXTENSION}'
        preview_basename = '' if infinite_preview is None else \
            f'preview{AUDIO_EXTENSION}' if not infinite_preview else f'preview_inf{AUDIO_EXTENSION}'
        jacket_basename = '' if jacket_idx is None else f'{jacket_idx}.png'

        header = f'''// Source: {str(self.game_id).zfill(3)}_{str(self.song_id).zfill(4)}_{self.get_metadata("ascii")}_{self.diff_token()}.vox
// Created by vox2ksh-{os.popen('git rev-parse HEAD').read()[:8].strip()}.
// Contact Nekoht#8008 on Discord for bug reports and assistance.
// previewfile and realdifficulty require a modified client to have any effect (the official releases of USC and KSM do
//   not have support for these fields).
title={self.get_metadata('title_name')}
artist={self.get_metadata('artist_name')}
effect={self.get_metadata('effected_by', True)}
jacket={jacket_basename}
illustrator={self.get_metadata('illustrator', True)}
difficulty={self.difficulty.to_ksh_name()}
realdifficulty={self.get_real_difficulty()}
level={self.get_metadata('difnum', True)}
t={self.bpm_string()}
m={track_basename}
mvol={self.get_metadata('volume')}
previewfile={preview_basename}
o=0
bg=desert
layer=arrow
po=0
plength=15000
pfiltergain=50
filtertype=peak
chokkakuautovol=0
chokkakuvol=50
ver=167'''

        print(header, file=file)

        print('--', file=file)

        class SpControllerCountdown(dataobject):
            event: CameraNode
            time_left: int

        # Below begins the main printing loop.
        # We iterate through each tick of the song and print a KSH line. If there are events, we put stuff on that line.

        # The currently active BT holds.
        holds = {}

        # The currently active SpController nodes.
        ongoing_spcontroller_events = {p: None for p in SpcParam}

        # Whether there is an ongoing laser on either side.
        lasers = {s: None for s in LaserSide}
        slam_status = {}
        last_laser_timing = {s: None for s in LaserSide}
        last_filter = KshFilter.PEAK
        current_timesig = self.events[Timing(1, 1, 0)][EventKind.TIMESIG]
        debug.current_line_num = len(header.split('\n')) + 1

        measure_iter = range(self.end.measure)

        for m in measure_iter:
            measure = m + 1

            print(f'// {measure}', file=file)

            # Laser range resets every measure in ksh.
            laser_range = {LaserSide.LEFT: 1, LaserSide.RIGHT: 1}

            now = Timing(measure, 1, 0)

            if now in self.events and EventKind.TIMESIG in self.events[now]:
                current_timesig = self.events[now][EventKind.TIMESIG]
                print(f'beat={current_timesig.top}/{current_timesig.bottom}', file=file)

            for b in range(current_timesig.top):
                # Vox beats are also 1-indexed.
                beat = b + 1

                for o in range(int(float(TICKS_PER_BEAT) * (4 / current_timesig.bottom))):
                    # However, vox offsets are 0-indexed.

                    now = Timing(measure, beat, o)

                    buffer = KshLineBuf()

                    if now in self.events:
                        for kind, event in self.events[now].items():
                            if kind == EventKind.TIMESIG and (beat != 1 or o != 0):
                                raise KshConvertError('time signature change in the middle of a measure')

                            elif kind == EventKind.BPM:
                                event: float
                                buffer.meta.append(f't={str(event).rstrip("0").rstrip(".").strip()}')

                            elif type(kind) is tuple and kind[0] == EventKind.SPCONTROLLER:
                                event: CameraNode
                                cam_param: SpcParam = kind[1]
                                if cam_param.to_ksh_value() is not None:
                                    if ongoing_spcontroller_events[cam_param] is not None and ongoing_spcontroller_events[cam_param].time_left != 0:
                                        raise KshConvertError(f'spcontroller node at {now} interrupts another of same kind ({cam_param})')
                                    ongoing_spcontroller_events[cam_param] = SpControllerCountdown(event=event, time_left=event.duration)
                                    buffer.meta.append(f'{cam_param.to_ksh_name()}={cam_param.to_ksh_value(event.start_param)}')

                            elif kind == EventKind.TILTMODE:
                                event: TiltMode
                                buffer.meta.append(f'tilt={event.to_ksh_name()}')

                            elif type(kind) is tuple and kind[0] == EventKind.TRACK:
                                if kind[1] == 1 or kind[1] == 8:
                                    # Laser
                                    if type(event) is LaserSlam:
                                        event: LaserSlam
                                        # TODO Laser countdown for different timesigs
                                        laser = event.start

                                        if event.side in map(lambda x: x.side(), slam_status):
                                            raise KshConvertError('new laser node spawn while trying to resolve slam')

                                        slam_status[event] = SLAM_TICKS

                                        if laser.roll_kind is not None:
                                            if buffer.spin != '':
                                                debug.record(Debug.Level.WARNING, 'ksh_laser', 'spin on both lasers')

                                            if laser.roll_kind.value <= 3:
                                                buffer.spin = '@'
                                                if event.direction() == LaserSlam.Direction.LEFT:
                                                    buffer.spin += '('
                                                else:
                                                    buffer.spin += ')'

                                                # My assumption right now is that the MEASURE kind will always take one
                                                # measure's worth of ticks. Likewise for the other ones.
                                                if laser.roll_kind == RollKind.MEASURE:
                                                    buffer.spin += str(int(current_timesig.top * current_timesig.ticks_per_beat() * 0.85))
                                                elif laser.roll_kind == RollKind.HALF_MEASURE:
                                                    buffer.spin += str(int((current_timesig.top * current_timesig.ticks_per_beat()) / 1.7))
                                                elif laser.roll_kind == RollKind.THREE_BEAT:
                                                    buffer.spin += str(int((current_timesig.top * current_timesig.ticks_per_beat()) * 0.62))

                                            elif laser.roll_kind == RollKind.CANCER:
                                                # TODO This roll.
                                                buffer.spin = '@'
                                                if event.direction() == LaserSlam.Direction.LEFT:
                                                    buffer.spin += '('
                                                else:
                                                    buffer.spin += ')'
                                                buffer.spin += str(current_timesig.top * current_timesig.ticks_per_beat() * 2)
                                            elif laser.roll_kind == RollKind.SWING:
                                                buffer.spin = '@'
                                                if event.direction() == LaserSlam.Direction.LEFT:
                                                    buffer.spin += '<'
                                                else:
                                                    buffer.spin += '>'
                                                buffer.spin += str(int((current_timesig.top * current_timesig.ticks_per_beat()) * 0.62))

                                        # noinspection PyUnusedLocal
                                        event: LaserNode = event.start

                                    event: LaserNode

                                    # KSH defines anything less than a 32th to be a slam, but some vox files
                                    # have nodes less than a 32th apart from each other. To counter this, we
                                    # just push laser nodes a tick forward until they're more than a 32th
                                    # apart.
                                    skip_laser = False
                                    thirtysecondth_ticks = int((4 * int(float(TICKS_PER_BEAT) * (4.0 / current_timesig.bottom))) / 32)
                                    if last_laser_timing[event.side] is not None and now.diff(last_laser_timing[event.side], current_timesig) == thirtysecondth_ticks:
                                        # Push it a tick forward to avoid being interpreted as a slam.
                                        if now.add(1, current_timesig) not in self.events:
                                            self.events[now.add(1, current_timesig)] = {}
                                        self.events[Timing(now.measure, now.beat, now.offset + 1)][kind] = event
                                        skip_laser = True

                                        # Look ahead and push other nodes forward.
                                        no_more_pushes = False
                                        while not no_more_pushes:
                                            no_more_pushes = True
                                            for i in range(6, 1, -1):
                                                lookahead_timing = now.add(1, current_timesig).add(i, current_timesig)
                                                if lookahead_timing in self.events and kind in self.events[lookahead_timing]:
                                                    ev = self.events[lookahead_timing][kind]
                                                    timing_plus_one = lookahead_timing.add(1, current_timesig)
                                                    if timing_plus_one not in self.events:
                                                        self.events[timing_plus_one] = {}
                                                    self.events[Timing(lookahead_timing.measure, lookahead_timing.beat, lookahead_timing.offset + 1)][kind] = ev
                                                    del self.events[lookahead_timing][kind]
                                                    no_more_pushes = False

                                    if event.range != 1:
                                        buffer.meta.append(f'laserrange_{event.side.to_letter()}={event.range}x')
                                        laser_range[event.side] = event.range

                                    if event.filter != last_filter:
                                        buffer.meta.append(f'filtertype={event.filter.to_ksh_name()}')
                                        last_filter = event.filter

                                    if not skip_laser:
                                        if event.node_cont == LaserCont.START:
                                            lasers[event.side] = True
                                        elif event.node_cont == LaserCont.END:
                                            lasers[event.side] = False
                                        buffer.lasers[event.side] = event.position_ksh()

                                    last_laser_timing[event.side] = now

                                else:
                                    # Button
                                    event: ButtonPress
                                    if event.duration != 0:
                                        if event.button.is_fx():
                                            letter = 'l' if event.button == Button.FX_L else 'r'
                                            try:
                                                event.effect: KshEffect
                                                effect_string = f'{self.effect_defines[event.effect].fx_change(event.effect)}' if type(event.effect) is int else \
                                                    event.effect[0].to_ksh_name(event.effect[1])
                                                buffer.meta.append(f'fx-{letter}={effect_string}')
                                            except KeyError:
                                                debug.record_last_exception(tag='button_fx')
                                        buffer.buttons[event.button] = KshLineBuf.ButtonState.HOLD
                                        holds[event.button] = event.duration
                                    elif args.do_media:
                                        # Check for a chip sound.
                                        buffer.buttons[event.button] = KshLineBuf.ButtonState.PRESS
                                        event.effect: int
                                        if event.button.is_fx() and event.effect is not None:
                                            letter = 'l' if event.button == Button.FX_L else 'r'
                                            buffer.meta.append(f'fx-{letter}_se={event.effect}{FX_CHIP_SOUND_EXTENSION}')

                    # Loop end stuff.
                    for cam_param in [x for x in ongoing_spcontroller_events.keys() if ongoing_spcontroller_events[x] is not None]:
                        if ongoing_spcontroller_events[cam_param].time_left == 0:
                            # SpController node ended and there's not another one after.
                            event: CameraNode = ongoing_spcontroller_events[cam_param].event
                            buffer.meta.append(f'{cam_param.to_ksh_name()}={cam_param.to_ksh_value(event.end_param)}')
                            ongoing_spcontroller_events[cam_param] = None
                        else:
                            ongoing_spcontroller_events[cam_param].time_left -= 1

                    holds_temp = holds.copy()
                    for button in holds.keys():
                        if holds_temp[button] == 0:
                            del holds_temp[button]
                        else:
                            buffer.buttons[button] = KshLineBuf.ButtonState.HOLD
                            holds_temp[button] -= 1
                    holds = holds_temp

                    for side in LaserSide:
                        if buffer.lasers[side] == '-' and lasers[side]:
                            buffer.lasers[side] = ':'

                    for slam in reversed(list(slam_status.keys())):
                        if slam_status[slam] == 0:
                            buffer.lasers[slam.side()] = slam.end.position_ksh()
                            del slam_status[slam]
                            if slam.end.node_cont == LaserCont.END:
                                lasers[slam.side()] = False
                        else:
                            if slam_status[slam] < SLAM_TICKS:
                                buffer.lasers[slam.side()] = ':'
                            slam_status[slam] -= 1

                    out = buffer.out()

                    print(out, file=file)

                    debug.current_line_num += len(out.split('\n'))

            print('--', file=file)

            debug.current_line_num += 1

        for k, v in self.effect_defines.items():
            print(v.to_define_line(k), file=file)

    def close(self):
        self.voxfile.close()


METADATA_FIX = [
    ['\u203E', '~'],
    ['\u301C', '～'],
    ['\u49FA', 'ê'],
    ['\u5F5C', 'ū'],
    ['\u66E6', 'à'],
    ['\u66E9', 'è'],
    ['\u7F47', 'ê'],
    ['\u8E94', '🐾'],
    ['\u9A2B', 'á'],
    ['\u9A69', 'Ø'],
    ['\u9A6B', 'ā'],
    ['\u9A6A', 'ō'],
    ['\u9AAD', 'ü'],
    ['\u9B2F', 'ī'],
    ['\u9EF7', 'ē'],
    ['\u9F63', 'Ú'],
    ['\u9F67', 'Ä'],
    ['\u973B', '♠'],
    ['\u9F6A', '♣'],
    ['\u9448', '♦'],
    ['\u9F72', '♥'],
    ['\u9F76', '♡'],
    ['\u9F77', 'é'],
    ['?壬', 'êp']
]

CASES = {
    'basic': (781, 'm'),
    'laser-range': (1138, 'e'),
    'laser-range-refreshing-fix': (980, 'e'),
    'choppy-laser': (1332, 'a'),
    'slam-range': (529, 'e'),
    'time-signature': (56, 'i'),
    'time-six-eight': (744, 'e'),
    'bpm': (262, 'n'),
    'early-version': (1, 'n'),
    'encoding': (656, 'e'),
    'diff-preview': (26, 'i'),
    'new-fx': (1, 'i'),
    'crash-fx': (1208, 'e'),
    'double-fx': (1136, 'a'),
    'highpass-fx': (1014, 'm'),
    'old-vox-retrigger-fx': (71, 'e'),
    'fx-chip-sound': (1048, 'm'),
    'basic-rolls': (271, 'e'),
    'camera': (250, 'i'),
    'tilt-mode': (34, 'i'),
    'spc-tilt': (71, 'i'),
    'wtf': (1361, 'm'),
}

def do_copy_audio(vox, out_dir, id_audio_map):
    """
    Search for and copy the track's audio file to the output directory.
    :return: True if the track is using an `_inf` audio file, False otherwise.
    """
    global args

    target_audio_path = f'{out_dir}/track.ogg'

    src_audio_path = f'{args.audio_dir}/{id_audio_map[vox.song_id]}'

    using_inf_audio = False

    if vox.difficulty == Difficulty.INFINITE:
        src_audio_path_diff = f'{splitx(src_audio_path)[0]}_4i{splitx(src_audio_path)[1]}'
        if os.path.exists(src_audio_path_diff):
            print(f'> Found difficulty-specific audio "{src_audio_path_diff}".')
            src_audio_path = src_audio_path_diff
            target_audio_path = f'{splitx(target_audio_path)[0]}_inf{splitx(target_audio_path)[1]}'
            using_inf_audio = True

    if not os.path.exists(target_audio_path):
        print(f'> Copying audio file "{src_audio_path}" to song directory.')
        copyfile(src_audio_path, target_audio_path)
    else:
        print(f'> Audio file "{target_audio_path}" already exists.')

    return using_inf_audio

def do_copy_jacket(vox, out_dir):
    """
    Find and copy the jacket image file for this vox to the output directory.
    :return: The index of the jacket used by this vox.
    """
    global args

    src_jacket_token = f'jk_{str(vox.game_id).zfill(3)}_{str(vox.song_id).zfill(4)}_{vox.difficulty.to_jacket_ifs_numer()}_b'
    src_jacket_path = args.jacket_dir + '/' + src_jacket_token + '_ifs/tex/' + src_jacket_token + '.png'

    if os.path.exists(src_jacket_path):
        target_jacket_path = f'{out_dir}/{str(vox.difficulty.to_jacket_ifs_numer())}.png'
        print(f'> Jacket image file found at "{src_jacket_path}". Copying to "{target_jacket_path}".')
        copyfile(src_jacket_path, target_jacket_path)
    else:
        print(f'> Could not find jacket image file. Checking easier diffs.')
        fallback_jacket_diff_idx = vox.difficulty.to_jacket_ifs_numer() - 1

        while True:
            if fallback_jacket_diff_idx < 0:
                print('> No jackets found for easier difficulties either. Leaving jacket blank.')
                return -1

            easier_jacket_path = f'{out_dir}/{fallback_jacket_diff_idx}.png'
            if os.path.exists(easier_jacket_path):
                # We found the diff number with the jacket.
                print(f'> Using jacket "{easier_jacket_path}".')
                return fallback_jacket_diff_idx
            fallback_jacket_diff_idx -= 1

    return vox.difficulty.to_jacket_ifs_numer()

def do_copy_preview(vox, out_dir):
    """
    Find and copy the preview for this vox to the output directory.
    :return: True if this chart has a difficulty-specific preview file, False otherwise.
    """
    global args

    output_path = f'{out_dir}/preview{AUDIO_EXTENSION}'
    preview_path = f'{args.preview_dir}/{vox.song_id}{AUDIO_EXTENSION}'
    diff_preview_path = f'{splitx(preview_path)[0]}_{vox.diff_token()}{AUDIO_EXTENSION}'
    using_difficulty_preview = False

    if os.path.exists(diff_preview_path):
        preview_path = diff_preview_path
        output_path = f'{splitx(output_path)[0]}_{vox.diff_token()}{splitx(output_path)[1]}'
        using_difficulty_preview = True

    if os.path.exists(output_path):
        print(f'> Preview file "{output_path}" already exists.')
        return

    if os.path.exists(preview_path):
        print(f'> Copying preview to "{output_path}".')
        copyfile(preview_path, output_path)
    else:
        print('> No preview file found.')

    return using_difficulty_preview

def do_copy_fx_chip_sounds(vox, out_dir):
    """ For each FX chip sound used in the chart, copy the sound file to the output directory. """
    global args
    global debug

    print(f'> Copying FX chip sounds {vox.required_chip_sounds}.')
    for sound in vox.required_chip_sounds:
        src_path = f'{args.fx_chip_sound_dir}/{sound}{FX_CHIP_SOUND_EXTENSION}'
        target_path = f'{out_dir}/{sound}{FX_CHIP_SOUND_EXTENSION}'
        if os.path.exists(src_path):
            copyfile(src_path, target_path)
        else:
            debug.record(Debug.Level.ERROR, 'copy_fx_chip_sound', f'cannot find file for chip sound with id {sound}')
            copyfile(f'{args.fx_chip_sound_dir}/0{FX_CHIP_SOUND_EXTENSION}', target_path)

##############
# PROGRAM RUNTIME BEGINS BELOW
#############

args = None
debug = None

def main():
    global args
    argparser = argparse.ArgumentParser(description='Convert vox to ksh')
    argparser.add_argument('-c', '--num-cores', default=3, type=int)
    argparser.add_argument('-t', '--testcase')
    argparser.add_argument('-i', '--song-id')
    argparser.add_argument('-d', '--song-difficulty')
    argparser.add_argument('-n', '--no-media', action='store_false', dest='do_media')
    argparser.add_argument('-m', '--no-convert', action='store_false', dest='do_convert')
    argparser.add_argument('-A', '--audio-dir', default='D:/SDVX-Extract/song')
    argparser.add_argument('-C', '--fx-chip-sound-dir', default='D:/SDVX-Extract/fx_chip_sound')
    argparser.add_argument('-J', '--jacket-dir', default='D:/SDVX-Extract/jacket')
    argparser.add_argument('-P', '--preview-dir', default='D:/SDVX-Extract/preview')
    args = argparser.parse_args()

    id_audio_map = {}

    if args.do_media:
        print('Generating audio file mapping...')
        # Audio files should have a name starting with the ID followed by a space.
        for _, _, files in os.walk(args.audio_dir):
            for f in files:
                if os.path.basename(f) == 'jk':
                    continue
                try:
                    if splitx(f)[1] == '.ogg' and not f.endswith('_4i.ogg'):
                        id_audio_map[int(splitx(os.path.basename(f))[0])] = f
                except ValueError as e:
                    print(e)
        print(f'{len(id_audio_map)} songs processed.')

    if args.testcase:
        if not args.testcase in CASES:
            print('please specify a valid testcase', file=sys.stderr)
            print('valid testcases are:', file=sys.stderr)
            for c in CASES.keys():
                print('\t' + c, file=sys.stderr)
            exit(1)

    # Create output directory.
    if not os.path.exists('out'):
        print(f'Creating output directory.')
        os.mkdir('out')

    candidates = []

    for dirpath, dirnames, filenames in os.walk(VOX_ROOT):
        for filename in filter(lambda n: n.endswith('.vox'), filenames):
            import re
            fullpath = pjoin(dirpath, filename)
            if (args.song_id is None and args.testcase is None) or \
                    (args.song_id is not None and f'_{args.song_id.zfill(4)}_' in filename) or \
                    (args.testcase is not None and re.match(rf'^.*00[1-4]_0*{CASES[args.testcase][0]}_.*{CASES[args.testcase][1]}\.vox$', fullpath)):
                if args.song_difficulty is None or splitx(filename)[0][-1] == args.song_difficulty:
                    # See if this is overriding an earlier game's version of the chart.
                    try:
                        prev: str = next(filter(lambda n: n.split('/')[-1].split('_')[1] == filename.split('_')[1] and splitx(n)[0][-1] == splitx(filename)[0][-1], candidates))
                        if int(prev.split('/')[-1].split('_')[0]) < int(filename.split('_')[0]):
                            candidates.remove(prev)
                        else:
                            continue
                    except StopIteration:
                        # Not clashing with anything.
                        pass
                    except IndexError:
                        # Malformed file name.
                        pass

                    candidates.append(pjoin(dirpath, filename))

    print('The following files will be processed:')
    for f in candidates:
        print(f'\t{f}')

    groups = [[] for _ in range(args.num_cores)]
    for i, candidate in enumerate(candidates):
        groups[i % args.num_cores].append(candidate)

    print(f'Beginning conversion across {args.num_cores} cores.')

    core_num = 0
    for i in range(1, len(groups)):
        if os.fork() == 0:
            core_num = i
            break

    global debug
    debug = Debug(exceptions_file=f'exceptions_{core_num}.txt')

    # Load source directory.
    for vox_path in groups[core_num]:
        debug.state = Debug.State.INPUT
        debug.input_filename = vox_path
        debug.output_filename = None
        debug.reset()

        print(f'{vox_path}:')
        # noinspection PyBroadException
        try:
            vox = Vox.from_file(vox_path)
        except Exception:
            debug.record_last_exception(level=Debug.Level.ERROR, tag='vox_load')
            continue

        print(f'> Processing "{vox_path}": {str(vox)}')

        # First try to parse the file.
        try:
            vox.parse()
        except Exception as e:
            print(f'> Parsing vox file failed with "{str(e)}":\n{traceback.format_exc()}')
            debug.record_last_exception(level=Debug.Level.ERROR, tag='vox_parse', trace=True)
            continue

        # Make the output directory.
        song_dir = f'out/{vox.ascii}'
        if not os.path.isdir(song_dir):
            print(f'> Creating song directory "{song_dir}".')
            os.mkdir(song_dir)

        jacket_idx = None
        infinite_audio = None
        infinite_preview = None

        # Copy media files over.
        if args.do_media:
            infinite_audio = do_copy_audio(vox, song_dir, id_audio_map)
            jacket_idx = do_copy_jacket(vox, song_dir)
            infinite_preview = do_copy_preview(vox, song_dir)

            # Copy FX chip sounds.
            if len(vox.required_chip_sounds) > 0:
                do_copy_fx_chip_sounds(vox, song_dir)

        # Output the KSH chart.
        chart_path = f'{song_dir}/{vox.difficulty.to_xml_name()}.ksh'

        debug.output_filename = chart_path
        debug.state = Debug.State.OUTPUT

        if args.do_convert:
            print(f'> Writing KSH data to "{chart_path}".')
            with open(chart_path, "w+", encoding='utf-8') as ksh_file:
                try:
                    vox.write_to_ksh(jacket_idx=jacket_idx,
                                     infinite_audio=infinite_audio,
                                     infinite_preview=infinite_preview,
                                     file=ksh_file)
                except Exception as e:
                    print(f'Outputting to ksh failed with "{str(e)}"\n{traceback.format_exc()}\n')
                    debug.record_last_exception(level=Debug.Level.ERROR, tag='ksh_output', trace=True)
                    continue
                print(f'> Finished conversion with {debug.exceptions_count[Debug.Level.ABNORMALITY]} abnormalities, {debug.exceptions_count[Debug.Level.WARNING]} warnings, and {debug.exceptions_count[Debug.Level.ERROR]} errors.')
        else:
            print(f'> Skipping conversion step.')
        vox.close()

    debug.close()

    if core_num > 0:
        exit(0)
    else:
        for _ in range(1, args.num_cores):
            os.wait()

if __name__ == '__main__':
    main()
