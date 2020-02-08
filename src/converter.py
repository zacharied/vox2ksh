#!/usr/bin/env python3.7
from enum import Enum, auto
from glob import glob
import threading

from recordclass import dataobject
from xml.etree import ElementTree
import traceback
import random
import math
import shutil
import time
import configparser

import sys, os
import argparse

from os.path import splitext as splitx

import ksh_effects

# Ticks per a beat of /4 time
TICKS_PER_BEAT = 48

SLAM_TICKS = 4

FX_CHIP_SOUND_COUNT = 14

KSH_DEFAULT_FILTER_GAIN = 50
KSH_DEFAULT_SLAM_VOL = 40

EFFECT_FALLBACK_NAME = 'fallback'

AUDIO_EXTENSION = '.ogg'
FX_CHIP_SOUND_EXTENSION = '.wav'

FX_CHIP_SOUND_VOL_PERCENT = 27

MAX_MEASURES = 999

class Debug:
    class State(Enum):
        INPUT = auto()
        OUTPUT = auto()

    class Level(Enum):
        ABNORMALITY = 'abnormal'
        WARNING = 'warning'
        ERROR = 'error'

    def __init__(self, exceptions_file):
        self.state = None
        self.input_filename = None
        self.output_filename = None
        self.current_line_num = 0
        self.exceptions_count = {level: 0 for level in Debug.Level}
        self.exceptions_file = open(exceptions_file, 'w+')

    def reset(self):
        for level in self.Level:
            self.exceptions_count[level] = 0

    def close(self):
        self.exceptions_file.close()

    def current_filename(self):
        return self.input_filename if self.state == self.State.INPUT else self.output_filename

    def record(self, level, tag, message):
        self.exceptions_count[level] += 1
        print(f'{self.current_filename()}:{self.current_line_num}\n{level.value} / {tag}: {message}\n',
              file=self.exceptions_file)

    def has_issues(self):
        for level in self.Level:
            if self.exceptions_count[level] > 0:
                return True
        return False

    def record_last_exception(self, level=Level.WARNING, tag='python_exception', trace=False):
        self.record(level, tag, str(sys.exc_info()[1]) + '\n\tTraceback:\n' + '\n'.join(traceback.format_tb(sys.exc_info()[2])))

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
        # TODO check bounds using current time signature
        try:
            return cls(int(splitted[0]), int(splitted[1]), int(splitted[2]))
        except ValueError:
            return None
        except IndexError:
            return None

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
        elif vox_name == 'LaneY':
            return cls.LANE_Y

        raise ValueError(f'invalid camera param "{vox_name}"')

    def is_state(self):
        return self == self.LANE_Y

    def to_ksh_name(self):
        if self == self.ROT_X:
            return 'zoom_top'
        elif self == self.RAD_I:
            return 'zoom_bottom'
        elif self == self.TILT:
            return 'tilt'
        elif self == self.LANE_Y:
            return 'lane_toggle'
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
        elif self == self.LANE_Y:
            return int(val)
        return None

    ROT_X = auto()
    RAD_I = auto()
    REALIZE = auto()
    AIRL_SCAX = auto()
    AIRR_SCAX = auto()
    TILT = auto()
    LANE_Y = auto()

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
        # TODO Correct this so filter indices line up with the TAB EFFECT INFO instead of being hardcoded
        if filter_id == 0:
            return cls.PEAK
        elif filter_id == 1 or filter_id == 2:
            return cls.LOWPASS
        elif filter_id == 3 or filter_id == 4:
            return cls.HIGHPASS
        elif filter_id == 5:
            return cls.BITCRUSH
        elif filter_id == 6:
            # TODO Figure out how effect 6 (and up?) is assigned.
            return cls.PEAK
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

class KshEffectDefine:
    def __init__(self):
        self.effect = None
        self.main_param = None
        self.params = {}

    def define_line(self, index):
        param_str = ''
        for k, v in self.params.items():
            param_str += f';{k}={v}'
        return f'#define_fx {index} type={self.effect.to_ksh_simple_name()}{param_str}'

    def fx_change(self, index, duration=0):
        if self.effect == ksh_effects.KshEffectKind.TAPESTOP:
            # Math lol
            extra = f';{int(2500 / (duration + 10))}'
        else:
            extra = f';{self.main_param}' if self.main_param is not None else ''
        return f'{index}{extra}'

    @classmethod
    def from_pre_v4_vox_sound_id(cls, sound_id):
        """Generate an effect definition line from the old-style effect declaration."""
        effect = None
        main_param = None

        from ksh_effects import KshEffectKind as Kind

        if sound_id == 2:
            effect = Kind.RETRIGGER
            main_param = '8'
        elif sound_id == 3:
            effect = Kind.RETRIGGER
            main_param = '16'
        elif sound_id == 4:
            effect = Kind.GATE
            main_param = '16'
        elif sound_id == 5:
            effect = Kind.FLANGER
            main_param = '200'
        elif sound_id == 6:
            effect = Kind.RETRIGGER
            main_param = '32'
        elif sound_id == 7:
            effect = Kind.GATE
            main_param = '8'
        elif sound_id == 8:
            effect = Kind.PITCHSHIFT
            main_param = '8' # TODO Tweak
        elif sound_id > 8:
            debug().record(Debug.Level.WARNING, 'fx_parse', f'old vox sound id {sound_id} unknown')

        return ksh_effects.KshEffect(effect, main_param=main_param) \
            if effect is not None and main_param is not None \
            else cls.default_effect()

    @classmethod
    def default_effect(cls):
        define = ksh_effects.KshEffect(ksh_effects.KshEffectKind.FLANGER, main_param='200')
        define.params['depth'] = f'{define.main_param}samples'
        return define

    @classmethod
    def from_effect_info_line(cls, line):
        splitted = line.replace('\t', '').split(',')

        if splitted[0] == '1':
            return ksh_effects.RetriggerEffect(*splitted[1:])
        elif splitted[0] == '2':
            return ksh_effects.GateEffect(*splitted[1:])
        elif splitted[0] == '3':
            return ksh_effects.PhaserEffect(*splitted[1:])
        elif splitted[0] == '4':
            return ksh_effects.TapestopEffect(*splitted[1:])
        elif splitted[0] == '5':
            return ksh_effects.SidechainEffect(*splitted[1:])
        elif splitted[0] == '6':
            return ksh_effects.WobbleEffect(*splitted[1:])
        elif splitted[0] == '7':
            return ksh_effects.BitcrusherEffect(*splitted[1:])
        elif splitted[0] == '8':
            return ksh_effects.UpdateableRetriggerEffect(*splitted[1:])
        elif splitted[0] == '9':
            return ksh_effects.PitchshiftEffect(*splitted[1:])
        elif splitted[0] == '11':
            return ksh_effects.LowpassEffect(*splitted[1:])
        elif splitted[0] == '12':
            return ksh_effects.FlangerEffect(*splitted[1:])
        else:
            raise ValueError(f'effect define id {splitted[0]} is not supported')

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
        chars = []
        for char in range(10):
            chars.append(chr(ord('0') + char))
        for char in range(24):
            chars.append(chr(ord('A') + char))
        for char in range(15):
            chars.append(chr(ord('a') + char))
        idx = math.ceil((self.position / 127) * (len(chars) - 1))
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
    NOVICE = 0, 'n', 'novice', 'nov'
    ADVANCED = 1, 'a', 'challenge', 'adv'
    EXHAUST = 2, 'e', 'extended', 'exh'
    INFINITE = 3, 'i', 'infinite', 'inf'
    MAXIMUM = 4, 'm', 'infinite', 'mxm'

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

    def to_abbreviation(self):
        return self.value[3]

class InfiniteVersion(Enum):
    INFINITE = 2, 'inf'
    GRAVITY = 3, 'grv'
    HEAVENLY = 4, 'hvn'
    VIVID = 5, 'vvd'

    @classmethod
    def from_inf_ver(cls, num):
        try:
            return next(x for x in cls if x.value[0] == num)
        except StopIteration:
            return None

    def to_abbreviation(self):
        return self.value[1]

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

class StopEvent:
    moment: Timing
    timesig: TimeSignature

class EventKind(Enum):
    TRACK = auto()
    TIMESIG = auto()
    BPM = auto()
    TILTMODE = auto()
    SPCONTROLLER = auto()
    STOP = auto()

class Background:
    # As of 2020-01-12, using the definitions from Lasergame.
    @staticmethod
    def from_vox_id(vox_id):
        """ Get the KSH name of the background corresponding to the ID. """
        if vox_id == 0 or vox_id == 1 or 14 <= vox_id == 16 or vox_id == 71:
            return 'techno'
        elif vox_id == 2 or vox_id == 6 or 11 <= vox_id <= 13:
            return 'wave'
        elif vox_id == 3 or vox_id == 7:
            return 'arrow'
        elif vox_id == 4 or vox_id == 8:
            # TODO It's kinda a pink version of 'wave'
            return 'sakura'
        elif vox_id == 63:
            return 'smoke'
        elif vox_id == 65:
            return 'snow'
        return 'fallback'

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
        self.source_file_name = None
        self.ascii = None
        self.game_id = 0
        self.song_id = 0
        self.vox_version = 0
        self.vox_defines = {} # defined in the vox file
        self.effect_defines = {} # will be defined in the ksh file
        self.effect_fallback = KshEffectDefine.default_effect()
        self.end = None
        self.events = {}

        self.last_time = Timing(1, 1, 0)
        self.new_laser = False

        self.state = None
        self.state_track = 0
        self.stop_point = None

        self.metadata: ElementTree = None
        self.difficulty = None
        self.difficulty_idx = 0

        self.finalized = False

        self.required_chip_sounds = set()

    def __str__(self):
        return f'{self.ascii} {self.diff_token()}'

    def diff_token(self):
        return str(self.difficulty_idx) + self.difficulty.to_letter()

    def diff_abbreviation(self):
        return self.difficulty.to_abbreviation() if self.difficulty != Difficulty.INFINITE else \
            InfiniteVersion.from_inf_ver(int(self.get_metadata('inf_ver'))).to_abbreviation()

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
            return next(iter([v for v in InfiniteVersion if v.value == int(self.get_metadata('inf_ver'))])).name.lower()
        return self.difficulty.name.lower()

    def has_event(self, event_kind):
        for v in self.events.values():
            if event_kind in v:
                return True
        return False

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

    def time_iter(self, start: Timing, timesig: TimeSignature):
        """
        Returns an iterator for the timing and event (or None) for each tick of the chart
        :param start: the Timing moment to start iterating from
        :param timesig: the timesig to start in
        :return: the iterator
        """
        if start.offset > timesig.ticks_per_beat():
            raise ValueError(f'start offset ({start.offset}) greater than ticks per beat ({timesig.ticks_per_beat()})')

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

                    yield (now, self.events[now] if now in self.events else None)

            m += 1

    @classmethod
    def from_file(cls, path):
        global args

        parser = Vox()

        file = open(path, 'r', encoding='cp932')
        parser.voxfile = file
        parser.source_file_name = os.path.split(path)[-1]

        filename_array = os.path.basename(path).split('_')
        for file in glob(f'{args.db_dir}/*.xml') if args.multi_db else [f'{args.db_dir}/music_db.xml']:
            with open(file, encoding='cp932') as db:
                try:
                    parser.game_id = int(filename_array[0])
                    parser.song_id = int(filename_array[1])
                    parser.difficulty = Difficulty.from_letter(os.path.splitext(path)[0][-1])
                    parser.difficulty_idx = os.path.splitext(path)[0][-2]
                except ValueError:
                    raise VoxLoadError(parser.voxfile.name, f'unable to parse difficulty from file name "{path}"')

                tree = ElementTree.fromstring(db.read()).findall('''.//music[@id='{}']'''.format(parser.song_id))

                if len(tree) > 0:
                    parser.metadata = tree[0]
                    break

        if parser.metadata is None:
            raise VoxLoadError(parser.voxfile.name, f'unable to find metadata for song')

        parser.ascii = parser.get_metadata('ascii')

        return parser

    def parse(self):
        line_no = 0
        section_line_no = 0

        for line in self.voxfile:
            section_line_no += 1
            line_no += 1
            debug().current_line_num = line_no

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
                    debug().record(Debug.Level.WARNING, 'fx_define', f'define line "{line}" does not have 3 operands')
                    continue

                self.vox_defines[splitted[1]] = int(splitted[2])
                if int(splitted[2]) != 0:
                    self.effect_defines[int(splitted[2])] = KshEffectDefine.from_pre_v4_vox_sound_id(int(splitted[2]))

            elif self.state is not None:
                self.process_state(line, section_line_no)

        self.finalized = True

    def process_state(self, line, section_line_num):
        splitted = line.split('\t')

        if line == '':
            return

        now = Timing.from_time_str(splitted[0])
        if now is not None:
            self.timing_point(now)

        if self.state == self.State.FORMAT_VERSION:
            self.vox_version = int(line)

        elif self.state == self.State.BEAT_INFO:
            timesig = TimeSignature(int(splitted[1]), int(splitted[2]))
            self.events[now][EventKind.TIMESIG] = timesig

        elif self.state == self.State.BPM:
            now = Timing(1, 1, 0)
            self.timing_point(now)
            try:
                self.events[now][EventKind.BPM] = float(line)
            except ValueError:
                # Jomanda adv seems to have the string "BAROFF" at one point.
                debug().record_last_exception(Debug.Level.ABNORMALITY, tag='bpm_parse')

        elif self.state == self.State.BPM_INFO:
            if splitted[2].endswith('-'):
                # There is a stop.
                self.stop_point = StopEvent()
                self.stop_point.moment = now
                last_timesig = None

                for t, e in self.time_iter(Timing(1, 1, 0), self.events[Timing(1, 1, 0)][EventKind.TIMESIG]):
                    if t.measure > MAX_MEASURES:
                        # When parsing a Stop event, the end of the chart may not yet be parsed, so we make an
                        # assumption for how long a chart could possibly be.
                        break

                    if e is not None and EventKind.TIMESIG in e:
                        last_timesig = e[EventKind.TIMESIG]
                    if e is not None and t == now:
                        self.stop_point.timesig = last_timesig

                if self.stop_point.timesig is None:
                    raise VoxParseError('bpm_info', 'unable to find end for stop event')
            else:
                if self.stop_point is not None:
                    self.events[self.stop_point.moment][EventKind.STOP] = now.diff(
                        self.stop_point.moment, self.stop_point.timesig)
                    self.stop_point = None
                if splitted[2] != '4' and splitted[2] != '4-':
                    debug().record(Debug.Level.ABNORMALITY, 'bpm_info', f'non-4 beat division in bpm info: {splitted[2]}')
                self.events[now][EventKind.BPM] = float(splitted[1])

        elif self.state == self.State.TILT_INFO:
            try:
                self.events[now][EventKind.TILTMODE] = TiltMode.from_vox_id(int(splitted[1]))
            except ValueError:
                debug().record_last_exception(level=Debug.Level.WARNING)

        elif self.state == self.State.END_POSITION:
            self.end = now

        elif self.state == self.State.SOUND_ID:
            # The `define` handler takes care of this outside of this loop.
            debug().record(Debug.Level.WARNING,
                         'vox_parse',
                         f'({self.state}) line other than a #define was encountered in SOUND ID')

        elif self.state == self.State.TAB_EFFECT:
            # TODO Tab effects
            if TabEffectInfo.line_is_abnormal(section_line_num, line):
                debug().record(Debug.Level.ABNORMALITY, 'tab_effect', f'tab effect info abnormal: {line}')

        elif self.state == self.State.FXBUTTON_EFFECT:
            if self.vox_version < 6:
                # Below v6, the defines come one after another with no spacing between other than the newline.
                try:
                    self.effect_defines[section_line_num - 1] = KshEffectDefine.from_effect_info_line(line)
                except ValueError:
                    self.effect_defines[section_line_num - 1] = KshEffectDefine.default_effect()
                    debug().record_last_exception(tag='fx_load')
            else:
                if (section_line_num - 1) % 3 < 2:
                    # The < 2 condition will allow the second line to override the first.
                    if line.isspace():
                        debug().record(Debug.Level.WARNING, 'fx_load', 'fx effect info line is blank')
                    elif splitted[0] != '0,':
                        index = int(section_line_num / 3)
                        try:
                            self.effect_defines[index] = KshEffectDefine.from_effect_info_line(line)
                        except ValueError:
                            self.effect_defines[index] = KshEffectDefine.default_effect()
                            debug().record_last_exception(level=Debug.Level.WARNING, tag='fx_load')

        elif self.state == self.State.TAB_PARAM_ASSIGN:
            if TabParamAssignInfo.line_is_abnormal(line):
                debug().record(Debug.Level.ABNORMALITY, 'tab_param_assign', f'tab param assign info abnormal: {line}')

        elif self.state == self.State.SPCONTROLLER:
            try:
                param = SpcParam.from_vox_name(splitted[1])
            except ValueError:
                debug().record_last_exception(tag='spcontroller_load')
                return

            if param is not None:
                try:
                    self.events[now][(EventKind.SPCONTROLLER, param)] = CameraNode(
                        float(splitted[4]), float(splitted[5]), int(splitted[3]))
                except ValueError:
                    # Just record it as an abnormality.
                    pass

            if SpcParam.line_is_abnormal(param, splitted):
                debug().record(Debug.Level.ABNORMALITY, 'spcontroller_load', 'spcontroller line is abnormal')

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
                        debug().record(Debug.Level.ABNORMALITY, 'roll_parse', f'roll type: {splitted[3]}')

                if len(splitted) > 4:
                    try:
                        laser_node.filter = KshFilter.from_vox_filter_id(int(splitted[4]))
                    except ValueError:
                        debug().record_last_exception(tag='laser_load')

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
                    try:
                        slam = LaserSlam(slam_start, laser_node)
                    except ValueError:
                        debug().record_last_exception(Debug.Level.WARNING, tag='slam_parse')
                        return
                    self.events[now][(EventKind.TRACK, self.state_track)] = slam

            else:
                try:
                    button = Button.from_track_num(self.state_track)
                except ValueError:
                    debug().record_last_exception(tag='button_load')
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
                            if 2 <= int(splitted[2]) <= 13:
                                # It's a regular effect.
                                fx_data = int(splitted[2]) - 2
                            elif int(splitted[2]) == 254:
                                debug().record(Debug.Level.WARNING,
                                             'button_fx',
                                             'reverb effect is unimplemented, using fallback')
                                fx_data = -1
                            else:
                                debug().record(Debug.Level.WARNING,
                                             'button_fx',
                                             'out of bounds fx index for FX hold, using fallback')
                                fx_data = -1
                    else:
                        # Fx chip, check for sound.
                        if self.vox_version >= 9:
                            sound_id = int(splitted[2])
                            if sound_id != -1 and sound_id != 255 and (sound_id >= FX_CHIP_SOUND_COUNT or sound_id < 0):
                                debug().record(Debug.Level.WARNING,
                                             'chip_sound_parse',
                                             f'unhandled chip sound id {sound_id}')
                            elif 1 <= sound_id < FX_CHIP_SOUND_COUNT:
                                fx_data = sound_id
                                self.required_chip_sounds.add(sound_id)

                self.events[now][(EventKind.TRACK, self.state_track)] = ButtonPress(button, int(splitted[1]), fx_data)


    def write_to_ksh(self, jacket_idx=None, using_difficulty_audio=None, file=sys.stdout):
        global args

        track_basename = f'track_{self.difficulty.to_abbreviation()}{AUDIO_EXTENSION}' if using_difficulty_audio else \
            f'track{AUDIO_EXTENSION}'
        jacket_basename = '' if jacket_idx is None else f'jacket_{jacket_idx}.png'

        track_bg = Background.from_vox_id(int(self.get_metadata('bg_no')))

        header = f'''// Source: {self.source_file_name}
// Created by vox2ksh-{os.popen('git rev-parse HEAD').read()[:8].strip()}.
title={self.get_metadata('title_name')}
artist={self.get_metadata('artist_name')}
effect={self.get_metadata('effected_by', True)}
sorttitle={self.get_metadata('title_yomigana')}
sortartist={self.get_metadata('artist_yomigana')}
jacket={jacket_basename}
illustrator={self.get_metadata('illustrator', True)}
difficulty={self.difficulty.to_ksh_name()}
level={self.get_metadata('difnum', True)}
t={self.bpm_string()}
m={track_basename}
mvol={self.get_metadata('volume')}
o=0
bg={track_bg}
layer={track_bg}
po={int(config['Audio']['hidden_preview_position']) * 1000}
plength=11000
pfiltergain={KSH_DEFAULT_FILTER_GAIN}
filtertype=peak
chokkakuautovol=0
chokkakuvol={KSH_DEFAULT_SLAM_VOL}
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
        debug().current_line_num = len(header.split('\n')) + 1

        measure_iter = range(self.end.measure)

        for m in measure_iter:
            measure = m + 1

            now = Timing(measure, 1, 0)

            # Laser range resets every measure in ksh.
            laser_range = {LaserSide.LEFT: 1, LaserSide.RIGHT: 1}

            if now in self.events and EventKind.TIMESIG in self.events[now]:
                current_timesig = self.events[now][EventKind.TIMESIG]
                print(f'beat={current_timesig.top}/{current_timesig.bottom}', file=file)

            for b in range(current_timesig.top):
                # Vox beats are also 1-indexed.
                beat = b + 1

                print(f'// #{measure},{beat}', file=file)

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

                            elif kind == EventKind.STOP:
                                event: int
                                buffer.meta.append(f'stop={event}')

                            elif type(kind) is tuple and kind[0] == EventKind.SPCONTROLLER:
                                event: CameraNode
                                cam_param: SpcParam = kind[1]
                                if cam_param.to_ksh_value() is not None:
                                    if ongoing_spcontroller_events[cam_param] is not None and ongoing_spcontroller_events[cam_param].time_left != 0:
                                        debug().record(Debug.Level.WARNING, 'spnode_output', f'spcontroller node at {now} interrupts another of same kind ({cam_param})')
                                    ongoing_spcontroller_events[cam_param] = SpControllerCountdown(event=event, time_left=event.duration)
                                    buffer.meta.append(f'{cam_param.to_ksh_name()}={cam_param.to_ksh_value(event.start_param)}')
                                elif cam_param.is_state():
                                    buffer.meta.append(f'{cam_param.to_ksh_name()}={event.duration}')

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
                                                debug().record(Debug.Level.WARNING, 'ksh_laser', 'spin on both lasers')

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
                                                    buffer.spin += str(int((current_timesig.top * current_timesig.ticks_per_beat()) / 2.95))
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

                                    if event.node_cont != LaserCont.END and event.filter != last_filter:
                                        if last_filter is None:
                                            buffer.meta.append(f'pfiltergain={KSH_DEFAULT_FILTER_GAIN}')

                                        if event.filter is None:
                                            buffer.meta.append(f'pfiltergain=0')
                                        else:
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
                                                if type(event.effect) is int:
                                                    effect_string = self.effect_defines[event.effect].fx_change(event.effect, duration=event.duration) if event.effect >= 0 else self.effect_fallback.fx_change(EFFECT_FALLBACK_NAME)
                                                else:
                                                    effect_string = event.effect[0].to_ksh_name(event.effect[1])
                                                buffer.meta.append(f'fx-{letter}={effect_string}')
                                            except KeyError:
                                                debug().record_last_exception(tag='button_fx')
                                        buffer.buttons[event.button] = KshLineBuf.ButtonState.HOLD
                                        holds[event.button] = event.duration
                                    elif args.do_media:
                                        # Check for a chip sound.
                                        buffer.buttons[event.button] = KshLineBuf.ButtonState.PRESS
                                        event.effect: int
                                        if event.button.is_fx() and event.effect is not None:
                                            letter = 'l' if event.button == Button.FX_L else 'r'
                                            buffer.meta.append(f'fx-{letter}_se=fxchip_{event.effect}{FX_CHIP_SOUND_EXTENSION};{FX_CHIP_SOUND_VOL_PERCENT}')

                    # Loop end stuff.
                    for cam_param in [x for x in ongoing_spcontroller_events.keys() if ongoing_spcontroller_events[x] is not None]:
                        if ongoing_spcontroller_events[cam_param].time_left == 0 and not cam_param.is_state():
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

                    debug().current_line_num += len(out.split('\n'))

            print('--', file=file)

            debug().current_line_num += 1

        for k, v in self.effect_defines.items():
            print(v.define_line(k), file=file)
        print(self.effect_fallback.define_line(EFFECT_FALLBACK_NAME), file=file)

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
    'laser-effect-6': (122, 'i'),
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
    'removed-data': (233, 'e'),
    'timesig-stop': (1148, 'm'),
    'laser-centering': (1244, 'm')
}

thread_id_index = {}
def thread_print(line):
    if threading.get_ident() not in thread_id_index:
        thread_id_index[threading.get_ident()] = len(thread_id_index) + 1
    print(f'{thread_id_index[threading.get_ident()]}> {line}')

def do_process_voxfiles(files):
    global args

    # Load source directory.
    for vox_path in files:
        try:
            debug().state = Debug.State.INPUT
            debug().input_filename = vox_path
            debug().output_filename = None
            debug().reset()

            # noinspection PyBroadException
            try:
                vox = Vox.from_file(vox_path)
            except Exception:
                debug().record_last_exception(level=Debug.Level.ERROR, tag='vox_load')
                continue

            thread_print(f'Processing "{vox_path}": {str(vox)}')

            start_time = time.time()

            # First try to parse the file.
            try:
                vox.parse()
            except Exception as e:
                thread_print(f'Parsing vox file failed with "{str(e)}":\n{traceback.format_exc()}')
                debug().record_last_exception(level=Debug.Level.ERROR, tag='vox_parse', trace=True)
                continue

            # Make the output directory.
            song_dir = f'out/{vox.ascii}'
            if not os.path.isdir(song_dir):
                thread_print(f'Creating song directory "{song_dir}".')
                os.mkdir(song_dir)

            jacket_idx = None
            using_difficulty_audio = None

            # Copy media files over.
            if args.do_media:
                using_difficulty_audio = do_copy_audio(vox, song_dir)
                jacket_idx = do_copy_jacket(vox, song_dir)

                # Copy FX chip sounds.
                if len(vox.required_chip_sounds) > 0:
                    do_copy_fx_chip_sounds(vox, song_dir)

            # Output the KSH chart.
            chart_path = f'{song_dir}/chart_{vox.diff_abbreviation()}.ksh'

            debug().output_filename = chart_path
            debug().state = Debug.State.OUTPUT

            if args.do_convert:
                thread_print(f'Writing KSH data to "{chart_path}".')
                with open(chart_path, "w+", encoding='utf-8') as ksh_file:
                    try:
                        vox.write_to_ksh(jacket_idx=jacket_idx,
                                         using_difficulty_audio=using_difficulty_audio,
                                         file=ksh_file)
                    except Exception as e:
                        print(f'Outputting to ksh failed with "{str(e)}"\n{traceback.format_exc()}\n')
                        debug().record_last_exception(level=Debug.Level.ERROR, tag='ksh_output', trace=True)
                        continue
                    duration = time.time() - start_time
                    if debug().has_issues():
                        exceptions = debug().exceptions_count
                        thread_print(f'Finished conversion in {truncate(duration, 4)}s with {exceptions[Debug.Level.ABNORMALITY]} abnormalities, {exceptions[Debug.Level.WARNING]} warnings, and {exceptions[Debug.Level.ERROR]} errors.')
                    else:
                        thread_print(f'Finished conversion in {truncate(duration, 4)}s with no issues.')
            else:
                thread_print(f'Skipping conversion step.')
            vox.close()
        except Exception as e:
            debug().record_last_exception(Debug.Level.ERROR, 'other', f'an error occurred: {str(e)}')

def do_copy_audio(vox, out_dir):
    """
    Search for and copy the track's audio file to the output directory.
    :return: True if the audio file is difficulty-specific, otherwise False.
    """
    global args

    using_difficulty_audio = False

    target_audio_path = f'{out_dir}/track.ogg'

    src_audio_path = f'{args.audio_dir}/{vox.song_id}_{vox.difficulty.to_abbreviation()}{AUDIO_EXTENSION}'

    if not os.path.exists(src_audio_path):
        src_audio_path = f'{args.audio_dir}/{vox.song_id}{AUDIO_EXTENSION}'
    else:
        using_difficulty_audio = True
        target_audio_path = f'{out_dir}/track_{vox.difficulty.to_abbreviation()}{AUDIO_EXTENSION}'
        thread_print(f'Found difficulty-specific audio "{src_audio_path}".')

    if not os.path.exists(src_audio_path):
        raise VoxLoadError('no audio file found')

    if not os.path.exists(target_audio_path):
        thread_print(f'Copying audio file "{src_audio_path}" to song directory.')
        shutil.copyfile(src_audio_path, target_audio_path)
    else:
        thread_print(f'Audio file "{target_audio_path}" already exists.')

    return using_difficulty_audio

def do_copy_jacket(vox, out_dir):
    """
    Find and copy the jacket image file for this vox to the output directory.
    :return: The index of the jacket used by this vox.
    """
    global args

    src_jacket_path = f'{args.jacket_dir}/{vox.song_id}_{vox.difficulty.to_jacket_ifs_numer()}.png'

    if os.path.exists(src_jacket_path):
        target_jacket_path = f'{out_dir}/jacket_{str(vox.difficulty.to_jacket_ifs_numer())}.png'
        thread_print(f'Jacket image file found at "{src_jacket_path}". Copying to "{target_jacket_path}".')
        shutil.copyfile(src_jacket_path, target_jacket_path)
    else:
        thread_print(f'Could not find jacket image file. Checking easier diffs.')
        fallback_jacket_diff_idx = vox.difficulty.to_jacket_ifs_numer() - 1

        while True:
            if fallback_jacket_diff_idx < 0:
                thread_print('No jackets found for easier difficulties either. Leaving jacket blank.')
                debug().record(Debug.Level.WARNING, 'copy_jacket', 'could not find any jackets to copy')
                return None

            easier_jacket_path = f'{args.jacket_dir}/{vox.song_id}_{fallback_jacket_diff_idx}.png'
            target_jacket_path = f'{out_dir}/jacket_{fallback_jacket_diff_idx}.png'
            if os.path.exists(easier_jacket_path):
                # We found the diff number with the jacket.
                thread_print(f'Using jacket "{easier_jacket_path}".')
                shutil.copyfile(easier_jacket_path, target_jacket_path)
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
    diff_preview_path = f'{splitx(preview_path)[0]}_{vox.difficulty.to_abbreviation()}{AUDIO_EXTENSION}'
    using_difficulty_preview = False

    if os.path.exists(diff_preview_path):
        preview_path = diff_preview_path
        output_path = f'{splitx(output_path)[0]}_{vox.difficulty.to_abbreviation()}{splitx(output_path)[1]}'
        using_difficulty_preview = True

    if os.path.exists(output_path):
        thread_print(f'Preview file "{output_path}" already exists.')
        return using_difficulty_preview

    if os.path.exists(preview_path):
        thread_print(f'Copying preview to "{output_path}".')
        shutil.copyfile(preview_path, output_path)
    else:
        print('> No preview file found.')
        debug().record(Debug.Level.WARNING, 'preview_copy', 'could not find preview file')
        return None

    return using_difficulty_preview

def do_copy_fx_chip_sounds(vox, out_dir):
    """ For each FX chip sound used in the chart, copy the sound file to the output directory. """
    global args

    thread_print(f'Copying FX chip sounds {vox.required_chip_sounds}.')
    for sound in vox.required_chip_sounds:
        src_path = f'{args.fx_chip_sound_dir}/{sound}{FX_CHIP_SOUND_EXTENSION}'
        target_path = f'{out_dir}/fxchip_{sound}{FX_CHIP_SOUND_EXTENSION}'
        if os.path.exists(src_path):
            shutil.copyfile(src_path, target_path)
        else:
            debug().record(Debug.Level.ERROR, 'copy_fx_chip_sound', f'cannot find file for chip sound with id {sound}')
            shutil.copyfile(f'{args.fx_chip_sound_dir}/0{FX_CHIP_SOUND_EXTENSION}', target_path)

def debug():
    global debugs

    if threading.get_ident() not in debugs:
        debugs[threading.get_ident()] = Debug(f'debug/exceptions_{threading.get_ident()}.txt')
    return debugs[threading.get_ident()]

##############
# PROGRAM RUNTIME BEGINS BELOW
#############

args = None
debugs = {}
config = configparser.ConfigParser()

def main():
    global config
    if not os.path.exists('config.ini'):
        print('Please create a config.ini based off the provided sample.', file=sys.stderr)
        sys.exit(1)
    config.read('config.ini')
    global args
    argparser = argparse.ArgumentParser(description='Convert vox to ksh')
    argparser.add_argument('-j', '--num-cores', default=1, type=int)
    argparser.add_argument('-t', '--testcase')
    argparser.add_argument('-i', '--song-id')
    argparser.add_argument('-d', '--song-difficulty')
    argparser.add_argument('-n', '--no-media', action='store_false', dest='do_media')
    argparser.add_argument('-m', '--no-convert', action='store_false', dest='do_convert')
    argparser.add_argument('-x', '--no-merge-db', action='store_false', dest='multi_db')
    argparser.add_argument('-V', '--vox-dir', default='D:/SDVX-Extract/vox')
    argparser.add_argument('-D', '--db-dir', default='D:/SDVX-Extract/music_db')
    argparser.add_argument('-A', '--audio-dir', default='D:/SDVX-Extract/song_prepared')
    argparser.add_argument('-C', '--fx-chip-sound-dir', default='D:/SDVX-Extract/fx_chip_sound')
    argparser.add_argument('-J', '--jacket-dir', default='D:/SDVX-Extract/jacket')
    argparser.add_argument('-P', '--preview-dir', default='D:/SDVX-Extract/preview')
    argparser.add_argument('-c', '--clean-output', action='store_true', dest='do_clean_output')
    argparser.add_argument('-e', '--clean-debug', action='store_true', dest='do_clean_debug')
    args = argparser.parse_args()

    if args.testcase:
        if not args.testcase in CASES:
            print('please specify a valid testcase', file=sys.stderr)
            print('valid testcases are:', file=sys.stderr)
            for c in CASES.keys():
                print('\t' + c, file=sys.stderr)
            exit(1)

    # Create output directory.
    if args.do_clean_output:
        print('Cleaning directory of old charts.')
        shutil.rmtree('out')
    if not os.path.exists('out'):
        print(f'Creating output directory.')
        os.mkdir('out')

    if args.do_clean_debug:
        print('Cleaning directory of debug output.')
        shutil.rmtree('debug')
    if not os.path.exists('debug'):
        print(f'Creating debug output directory.')
        os.mkdir('debug')

    candidates = []

    print(f'Finding vox files.')

    for filename in glob(f'{args.vox_dir}/*.vox'):
        import re
        if (args.song_id is None and args.testcase is None) or \
                (args.song_id is not None and f'_{args.song_id.zfill(4)}_' in filename) or \
                (args.testcase is not None and re.match(rf'^.*00[1-4]_0*{CASES[args.testcase][0]}_.*{CASES[args.testcase][1]}\.vox$', filename)):
            if args.song_difficulty is None or splitx(filename)[0][-1] == args.song_difficulty:
                # See if this is overriding an earlier game's version of the chart.
                try:
                    prev: str = next(filter(lambda n: n.split('_')[1] == filename.split('_')[1] and splitx(n)[0][-1] == splitx(filename)[0][-1], candidates))
                    if int(prev.split('_')[1]) < int(filename.split('_')[1]):
                        candidates.remove(prev)
                    else:
                        continue
                except StopIteration:
                    # Not clashing with anything.
                    pass
                except (IndexError, ValueError):
                    # Malformed file name.
                    pass

                candidates.append(filename)

    print('The following files will be processed:')
    for f in candidates:
        print(f'\t{f}')

    groups = [[] for _ in range(args.num_cores)]
    for i, candidate in enumerate(candidates):
        try:
            song_id = os.path.basename(candidate).split('_')[1]
            groups[int(song_id) % args.num_cores].append(candidate)
        except (ValueError, IndexError):
            groups[i % args.num_cores].append(candidate)

    threads = []

    global debugs

    for i in range(args.num_cores):
        thread = threading.Thread(target=do_process_voxfiles, args=(groups[i],), name=f'Thread-{i}')
        threads.append(thread)

    print(f'Performing conversion across {args.num_cores} threads.')

    for t in threads:
        t.start()
    for t in threads:
        t.join()
    for d in debugs.values():
        d.close()

if __name__ == '__main__':
    main()
