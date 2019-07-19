from enum import Enum, auto
from collections import namedtuple
from recordclass import dataobject
from xml.etree import ElementTree
from shutil import copyfile
from functools import lru_cache as cache
from os.path import splitext as splitx
import traceback
import random
import math

import sys, os
import argparse

TICKS_PER_BEAT = 48

exceptions_file = None
def write_to_exceptions(filename, line_no=None, is_input_error=False, is_critical=False):
    if exceptions_file is not None:
        print(f'{"[IN]" if is_input_error else "[OUT]"} {"*** " if is_critical else ""}{filename}{":" + str(line_no) if line_no is not None else ""}:\n{traceback.format_exc()}', file=exceptions_file)

class RevMap:
    def __init__(self, mapping):
        self.mapping = mapping
        self._rev = {v: k for k, v in mapping.items()}

    def get(self, k):
        return self.mapping.get(k)

    def rev(self, k):
        return self._rev.get(k)

TimeSignature = namedtuple('TimeSignature', 'top bottom')

class VoxLoadError(Exception):
    def __init__(self, file, msg):
        self.file = file
        self.msg = msg

    def __str__(self):
        return f'{self.file}: {self.msg}'

class VoxParseError(Exception):
    def __init__(self, file, section, line, msg):
        self.file = file
        self.section = section
        self.line = line
        self.msg = msg

    def __str__(self):
        return f'{self.file}:{self.line} ({self.section}): {self.msg}'

class KshConvertError(Exception):
    def __init__(self, infile, outfile, line, msg):
        self.infile = infile
        self.outfile = outfile
        self.line = line
        self.msg = msg

    def __str__(self):
        return f'{self.infile}  ->  {self.outfile}:{self.line}: {self.msg}'

class Timing:
    def __init__(self, measure, beat, offset):
        self.measure = measure
        self.beat = beat
        self.offset = offset

    @classmethod
    def from_time_str(cls, time: str):
        """ Create a Timing from the format string that appears in the first column of vox tracks. """
        splitted = time.split(',')
        if int(splitted[2]) >= TICKS_PER_BEAT:
            raise ValueError(f'offset of {int(splitted[2])} is greater than maximum')
        return cls(int(splitted[0]), int(splitted[1]), int(splitted[2]))

    def __eq__(self, other):
        return self.measure == other.measure and self.beat == other.beat and self.offset == other.offset

    def __hash__(self):
        return hash((self.measure, self.beat, self.offset))

    def __str__(self):
        return '{},{},{}'.format(self.measure, self.beat, self.offset)

    def __cmp__(self, other):
        if self.measure == other.measure:
            if self.beat == other.beat:
                return self.offset - other.offset
            return self.beat - other.beat
        return self.measure - other.measure

class CameraParam(Enum):
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

        raise ValueError(f'invalid camera param "{vox_name}"')

    def to_ksh_name(self):
        if self == self.ROT_X:
            return 'zoom_top'
        elif self == self.RAD_I:
            return 'zoom_bottom'
        else:
            return None

    def scaling_factor(self):
        if self == self.ROT_X:
            return 150.0
        elif self == self.RAD_I:
            return -150.0
        return None

    ROT_X = auto()
    RAD_I = auto()
    REALIZE = auto()
    AIRL_SCAX = auto()
    AIRR_SCAX = auto()

def spcontroller_line_is_normal(param, splitted):
    cell = lambda i: splitted[i].strip()
    if param == CameraParam.REALIZE:
        if cell(2) == '3':
            return cell(4) == '36.12' and cell(5) == '60.12' and cell(6) == '110.12' and cell(7) == '0.00'
        elif cell(2) == '4':
            return cell(4) == '0.62' and cell(5) == '0.72' and cell(6) == '1.03' and cell(7) == '0.00'
    return True
    # TODO Other params maybe

class KshootFilter(Enum):
    @classmethod
    def from_vox_filter_id(cls, filter_id):
        if filter_id == 0:
            return cls.PEAK
        elif filter_id == 2:
            return cls.LOWPASS
        elif filter_id == 4:
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

class KshootEffect(Enum):
    @classmethod
    @cache(maxsize=None)
    def _name(cls):
        return RevMap({
            cls.RETRIGGER: 'Retrigger',
            cls.GATE: 'Gate',
            cls.FLANGER: 'Flanger',
            cls.TAPESTOP: 'TapeStop',
            cls.SIDECHAIN: 'SideChain',
            cls.WOBBLE: 'Wobble',
            cls.BITCRUSHER: 'BitCrusher',
            cls.ECHO: 'Echo',
            cls.PITCHSHIFT: 'PitchShift'
        })

    def to_ksh_simple_name(self):
        return self._name().get(self)

    @classmethod
    def choose_random(cls):
        choice = random.choice(list(cls))
        if choice == cls.TAPESTOP or choice == cls.PITCHSHIFT:
            return cls.choose_random()
        return choice

    RETRIGGER = auto()
    GATE = auto()
    FLANGER = auto()
    BITCRUSHER = auto()
    PHASER = auto()
    WOBBLE = auto()
    PITCHSHIFT = auto()
    TAPESTOP = auto()
    ECHO = auto()
    SIDECHAIN = auto()

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
        define = cls.default_effect()

        if sound_id == 2:
            define.effect = KshootEffect.RETRIGGER
            define.main_param = '1/8'
        elif sound_id == 3:
            define.effect = KshootEffect.RETRIGGER
            define.main_param = '1/16'
        elif sound_id == 4:
            define.effect = KshootEffect.GATE
            define.main_param = '1/16'
        elif sound_id == 5:
            define.effect = KshootEffect.FLANGER # TODO Tweak
        elif sound_id == 6:
            define.effect = KshootEffect.RETRIGGER
            define.main_param = '1/32'
        elif sound_id == 7:
            define.effect = KshootEffect.GATE
            define.main_param = '1/8'
        elif sound_id == 8:
            define.effect = KshootEffect.PITCHSHIFT
            define.main_param = '8' # TODO Tweak
        elif sound_id > 8:
            raise ValueError(f'old vox sound id {sound_id} does not exist')

        return define

    @classmethod
    def default_effect(cls):
        define = KshEffectDefine()
        define.effect = KshootEffect.FLANGER
        return define

    @classmethod
    def from_effect_info_line(cls, line):
        splitted = line.replace('\t', '').split(',')

        define = KshEffectDefine()
        define.effect = KshootEffect.FLANGER

        if splitted[0] == '1' or splitted[0] == '8':
            # Retrigger / echo (they're pretty much the same thing)
            define.effect = KshootEffect.RETRIGGER

            if float(splitted[3]) < 0:
                define.main_param = int(float(splitted[1]) * 16)
                define.params['waveLength'] = f"1/{define.main_param}"
                define.params['updatePeriod'] = "1/18"
            else:
                define.main_param = int((4 / float(splitted[3])) * float(splitted[1]))
                define.params['waveLength'] = f'1/{define.main_param}'
                define.params['updatePeriod'] = f"1/{4 / float(splitted[3])}"
            rate = f'{int(float(splitted[5]) * 100)}%'
            feedback_level = f'{int(float(splitted[4]) * 100)}%'

            define.params['mix'] = f'0%>{int(float(splitted[2]))}%'

            if feedback_level != '100%':
                define.effect = KshootEffect.ECHO
                define.params['feedbackLevel'] = feedback_level
                if splitted[0] == '8':
                    define.params['updatePeriod'] = 0
                define.params['updateTrigger'] = 'off>on' if splitted[0] == '8' else 'off'
            elif float(splitted[3]) < 0:
                define.params['rate'] = rate
            else:
                define.params['rate'] = rate
                if splitted[0] == '8':
                    define.params['updateTrigger'] = 'off>on'
                    define.params['updatePeriod'] = 0

        elif splitted[0] == '2':
            # Gate
            define.effect = KshootEffect.GATE
            define.main_param = (2 / float(splitted[3])) * float(splitted[2])
            define.params['waveLength'] = f'1/{define.main_param}'
            define.params['mix'] = f'0%>{int(float(splitted[1]))}%'

        elif splitted[0] == '3':
            # Flanger
            define.effect = KshootEffect.FLANGER
            define.params['delay'] = f'{int(float(splitted[2]) * 100)}samples'
            define.params['depth'] = f'{int(float(splitted[3]) * 100)}samples'
            define.params['feedback'] = f'{int(splitted[4])}%' # TODO Not sure about this one
            define.params['period'] = str(float(splitted[5]))
            define.params['volume'] = f'{min(int(float(splitted[1]) * 1.33), 100)}%' # Normally no multiplier but i like this

        elif splitted[0] == '4':
            # Tape stop
            define.effect = KshootEffect.TAPESTOP
            define.params['mix'] = f'0%>{int(float(splitted[1]))}%'
            speed = float(splitted[3]) * float(splitted[2]) * 9.8125
            if speed > 50:
                speed = 50
            else:
                speed = int(speed)
            define.main_param = speed
            define.params['speed'] = f'{define.main_param}%'

        elif splitted[0] == '5':
            # Sidechain
            define.effect = KshootEffect.SIDECHAIN
            define.main_param = int(float(splitted[2]) * 2)
            define.params['period'] = f'1/{define.main_param}'

        elif splitted[0] == '6':
            # Wobble
            define.effect = KshootEffect.WOBBLE
            define.main_param = int(float(splitted[6])) * 4
            define.params['waveLength'] = f'1/{define.main_param}'
            define.params['loFreq'] = f'{int(float(splitted[4]))}Hz'
            define.params['hiFreq'] = f'{int(float(splitted[5]))}Hz'
            define.params['Q'] = str(float(splitted[7]))
            define.params['mix'] = f'0%>{int(float(splitted[3]))}%'

        elif splitted[0] == '7':
            # Bitcrusher
            define.effect = KshootEffect.BITCRUSHER
            define.main_param = int(splitted[2])
            define.params['reduction'] = f'{define.main_param}samples'
            define.params['mix'] = f'0%>{int(float(splitted[1]))}%'

        elif splitted[0] == '9':
            # Pitchshift
            define.effect = KshootEffect.PITCHSHIFT
            define.main_param = int(float(splitted[2]))
            define.params['pitch'] = str(define.main_param)
            define.params['mix'] = f'0%>{int(float(splitted[1]))}'

        elif splitted[0] == '11':
            define.effect = KshootEffect.WOBBLE
            define.main_param = 1
            define.params['loFreq'] = f'{int(float(splitted[3]))}Hz'
            define.params['hiFreq'] = define.params["loFreq"]
            define.params['Q'] = '1.4'

        else:
            raise ValueError(f'effect define id {splitted[0]} is not supported')

        return define

class Button(Enum):
    BT_A = auto()
    BT_B = auto()
    BT_C = auto()
    BT_D = auto()
    FX_L = auto()
    FX_R = auto()

    def is_fx(self):
        return self == Button.FX_L or self == Button.FX_R

    @classmethod
    def from_track_num(cls, num: int):
        if num == 2:
            return cls.FX_L
        elif num == 3:
            return cls.BT_A
        elif num == 4:
            return cls.BT_B
        elif num == 5:
            return cls.BT_C
        elif num == 6:
            return cls.BT_D
        elif num == 7:
            return cls.FX_R
        elif num == 9:
            return None
        else:
            raise ValueError(f'invalid track number for button: {num}')

    def to_track_num(self):
        if self == self.FX_L:
            return 2
        elif self == self.FX_R:
            return 7
        elif self == self.BT_A:
            return 3
        elif self == self.BT_B:
            return 4
        elif self == self.BT_C:
            return 5
        elif self == self.BT_D:
            return 6

class ButtonPress:
    def __init__(self, button: Button, duration: int, effect):
        self.button = button
        self.duration = duration
        self.effect = effect

LaserRangeChange = namedtuple('LaserRangeChange', 'time side range')

class LaserSide(Enum):
    LEFT = auto()
    RIGHT = auto()

    def to_letter(self):
        return 'l' if self == LaserSide.LEFT else 'r'

    def to_track_num(self):
        return 1 if self == self.LEFT else 8

class LaserCont(Enum):
    """ The continuity status of a laser node. """
    CONTINUE = 0
    START = 1
    END = 2

class LaserNode:
    class Builder(dataobject):
        side: LaserSide = None
        position: int = None
        node_type: LaserCont = None
        range: int = 1
        filter: KshootFilter = KshootFilter.PEAK

    def __init__(self, builder: Builder):
        self.side = builder.side
        self.position = builder.position
        self.node_type = builder.node_type
        self.range = builder.range
        self.filter = builder.filter

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
        idx = math.floor((self.position / 127) * (len(chars) - 1))
        return chars[idx]

class LaserSlam:
    def __init__(self, start: LaserNode, end: LaserNode):
        self.start = start
        self.end = end

class Difficulty(Enum):
    NOVICE = 0
    ADVANCED = 1
    EXHAUST = 2
    MAXIMUM = 3
    INFINITE = 4
    # TODO GRV and HVN?

    @classmethod
    @cache(maxsize=None)
    def _letter(cls):
        return RevMap({
            cls.NOVICE: 'n',
            cls.ADVANCED: 'a',
            cls.EXHAUST: 'e',
            cls.MAXIMUM: 'm',
            cls.INFINITE: 'i'
        })

    @classmethod
    def from_letter(cls, k):
        return cls._letter().rev(k)

    def to_letter(self):
        return self._letter().get(self)

    def to_ksh_name(self):
        """ Convert to a name recognized by KSM. """
        if self == self.NOVICE:
            return 'novice'
        elif self == self.ADVANCED:
            return 'challenge'
        elif self == self.EXHAUST:
            return 'extended'
        elif self == self.MAXIMUM or self == self.INFINITE:
            return 'infinite'

    def to_xml_name(self):
        if self == self.NOVICE:
            return 'novice'
        elif self == self.ADVANCED:
            return 'advanced'
        elif self == self.EXHAUST:
            return 'exhaust'
        elif self == self.MAXIMUM:
            return 'maximum'
        elif self == self.INFINITE:
            return 'infinite'

    def to_jacket_ifs_numer(self):
        if self == self.NOVICE:
            return 1
        elif self == self.ADVANCED:
            return 2
        elif self == self.EXHAUST:
            return 3
        elif self == self.INFINITE:
            return 4
        else:
            return 5

class TiltMode(Enum):
    # TODO Tweak -- is biggest correct?
    NORMAL = auto()
    BIGGEST = auto()
    KEEP_BIGGEST = auto()

    @classmethod
    def from_vox_id(cls, id):
        if id == 0:
            return cls.NORMAL
        elif id == 1:
            return cls.BIGGEST
        elif id == 2:
            return cls.KEEP_BIGGEST
        return None

    def to_ksh_name(self):
        if self == self.NORMAL:
            return 'normal'
        elif self == self.BIGGEST:
            return 'biggest'
        elif self == self.KEEP_BIGGEST:
            return 'keep_biggest'
        return None

class EventKind(Enum):
    TRACK = auto()
    TIMESIG = auto()
    BPM = auto()
    TILTMODE = auto()
    SPCONTROLLER = auto()

class KshLineBuf:
    class ButtonState(Enum):
        NONE = auto()
        PRESS = auto()
        HOLD = auto()

    def __init__(self):
        self.buttons = {}
        self.lasers = {}
        self.meta = []

        for bt in list(Button):
            self.buttons[bt] = self.ButtonState.NONE

        for side in list(LaserSide):
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
            elif token == 'TRACK AUTO TAB':
                return None
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
        FXBUTTON_EFFECT = auto()
        TRACK = auto()
        SPCONTROLLER = auto()

    def __init__(self):
        self.voxfile = None
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
        self._events_track = {}
        self._events_bpm = []
        self._events_timesig = []
        self._events_tiltmode = []
        self._events_spcontroller = {}

        self.warnings = 0

    def __str__(self):
        return f'{self.get_metadata("ascii")} {self.diff_token()}'

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
        # TODO Make sure decimal BPM's are okay.
        if self.get_metadata('bpm_min') == self.get_metadata('bpm_max'):
            return int(int(self.get_metadata('bpm_min')) / 100)
        else:
            return f'{int(int(self.get_metadata("bpm_min")) / 100)}-{int(int(self.get_metadata("bpm_max")) / 100)}'

    def events_track(self, track):
        if track not in self._events_track:
            res = {k[1]: v for k, v in self.events.items() if type(k[0]) is tuple and k[0][0] == EventKind.TRACK and k[0][1] == track}
            if self.finalized:
                self._events_track[track] = res
            return res
        return self._events_track[track]

    def events_bpm(self):
        if len(self._events_bpm) == 0:
            res = {k[1]: v for k, v in self.events.items() if k[0] == EventKind.BPM}
            if self.finalized:
                self._events_bpm = res
            return res
        return self._events_bpm

    def events_timesig(self):
        if len(self._events_timesig) == 0:
            res = {k[1]: v for k, v in self.events.items() if k[0] == EventKind.TIMESIG}
            if self.finalized:
                self._events_timesig = res
            return res
        return self._events_timesig

    def events_tiltmode(self):
        if len(self._events_tiltmode) == 0:
            res = {k[1]: v for k, v in self.events.items() if k[0] == EventKind.TILTMODE}
            if self.finalized:
                self._events_tiltmode = res
            return res
        return self._events_tiltmode

    def events_spcontroller(self, control):
        if control not in self._events_spcontroller:
            res = {k[1]: v for k, v in self.events.items() if type(k[0]) is tuple and k[0][0] == EventKind.SPCONTROLLER and k[0][1] == control}
            if self.finalized:
                self._events_spcontroller[control] = res
            return res
        return self._events_spcontroller[control]

    def process_state(self, line, line_no, section_line_no):
        splitted = line.split('\t')

        filename = self.voxfile.name

        if line == '':
            return

        if self.state == self.State.FORMAT_VERSION:
            self.vox_version = int(line)

        elif self.state == self.State.BEAT_INFO:
            timesig = TimeSignature(int(splitted[1]), int(splitted[2]))
            self.events[(EventKind.TIMESIG, Timing.from_time_str(splitted[0]))] = timesig

        elif self.state == self.State.BPM:
            self.events[(EventKind.BPM, Timing(1, 1, 0))] = float(line)

        elif self.state == self.State.BPM_INFO:
            self.events[(EventKind.BPM, Timing.from_time_str(splitted[0]))] = splitted[1]

        elif self.state == self.State.TILT_INFO:
            try:
                self.events[(EventKind.TILTMODE, Timing.from_time_str(splitted[0]))] = TiltMode.from_vox_id(int(splitted[1]))
            except ValueError as e:
                raise VoxParseError(filename, str(self.state), line_no, str(e)) from e

        elif self.state == self.State.END_POSITION:
            self.end = Timing.from_time_str(line)

        elif self.state == self.State.SOUND_ID:
            raise VoxParseError(filename, str(self.state), line_no, 'non-define line encountered in SOUND ID')

        elif self.state == self.State.FXBUTTON_EFFECT:
            if self.vox_version < 6:
                try:
                    self.effect_defines[section_line_no - 1] = KshEffectDefine.from_effect_info_line(line)
                except ValueError as e:
                    self.effect_defines[section_line_no - 1] = KshEffectDefine.default_effect()
                    raise VoxParseError(filename, str(self.state), line_no, str(e)) from e
            else:
                if (section_line_no - 1) % 3 < 2:
                    # The < 2 condition will allow the second line to override the first.
                    if line.isspace():
                        raise VoxParseError(filename, str(self.state), line_no, 'fx effect info line is blank')
                    elif splitted[0] != '0,':
                        index = int(section_line_no / 3)
                        try:
                            self.effect_defines[index] = KshEffectDefine.from_effect_info_line(line)
                        except ValueError as e:
                            self.effect_defines[index] = KshEffectDefine.default_effect()
                            raise VoxParseError(filename, str(self.state), line_no, str(e)) from e

        elif self.state == self.State.SPCONTROLLER:
            try:
                param = CameraParam.from_vox_name(splitted[1])
            except ValueError as e:
                raise VoxParseError(filename, str(self.state), line_no, str(e)) from e
            if param is not None:
                self.events[((EventKind.SPCONTROLLER, param), Timing.from_time_str(splitted[0]))] = float(splitted[4])
            if not spcontroller_line_is_normal(param, splitted):
                raise VoxParseError(filename, str(self.state), line_no, f'line is abnormal: {line}')

        elif self.state == self.state.TRACK:
            if self.state_track == 1 or self.state_track == 8:
                laser_node = LaserNode.Builder()
                laser_node.side = LaserSide.LEFT if self.state_track == 1 else LaserSide.RIGHT
                laser_node.position = int(splitted[1])
                laser_node.node_type = LaserCont(int(splitted[2]))
                if len(splitted) > 4:
                    try:
                        laser_node.filter = KshootFilter.from_vox_filter_id(int(splitted[4]))
                    except ValueError:
                        write_to_exceptions(self.voxfile.name, line_no=line_no)

                if len(splitted) > 5:
                    laser_node.range = int(splitted[5])

                laser_node = LaserNode(laser_node)

                # Check if it's a slam.
                slam_start = self.events_track(self.state_track).get(Timing.from_time_str(splitted[0]))
                if Timing.from_time_str(splitted[0]) == self.last_time:
                    self.new_laser = False
                else:
                    self.new_laser = True

                events_key = ((EventKind.TRACK, self.state_track), Timing.from_time_str(splitted[0]))
                if self.new_laser:
                    self.events[events_key] = laser_node
                else:
                    if type(slam_start) is LaserSlam:
                        slam_start = slam_start.end
                    slam = LaserSlam(slam_start, laser_node)
                    self.events[events_key] = slam

                self.last_time = Timing.from_time_str(splitted[0])

            else:
                try:
                    button = Button.from_track_num(self.state_track)
                except ValueError as e:
                    raise VoxParseError(filename, str(self.state), line_no, str(e)) from e

                if button is None:
                    # Ignore track 9 buttons.
                    return

                fx_data = None
                if button.is_fx():
                    # Process effect assignment.
                    if self.vox_version < 4:
                        fx_data = int(splitted[3]) if splitted[3].isdigit() else int(self.vox_defines[splitted[3]])
                    else:
                        fx_data = int(splitted[2]) - 2

                self.events[((EventKind.TRACK, self.state_track), Timing.from_time_str(splitted[0]))] = ButtonPress(button, int(splitted[1]), fx_data)


    def write_to_ksh(self, file=sys.stdout, metadata_only=False, jacket_idx=None, progress_bar=True, track_basename=None, preview_basename=None):
        # First print metadata.
        if jacket_idx is None:
            jacket_idx = str(self.difficulty.to_jacket_ifs_numer())

        if track_basename is None:
            track_basename = f'track{AUDIO_EXTENSION}'

        if preview_basename is None:
            preview_basename = f'preview{AUDIO_EXTENSION}'

        print(f'''title={self.get_metadata('title_name')}
artist={self.get_metadata('artist_name')}
effect={self.get_metadata('effected_by', True)}
jacket={jacket_idx}.png
illustrator={self.get_metadata('illustrator', True)}
difficulty={self.difficulty.to_ksh_name()}
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
ver=167''', file=file)

        if metadata_only:
            return

        print('--', file=file)

        holds = {}
        lasers = {LaserSide.LEFT: False, LaserSide.RIGHT: False}
        slam_status = {}
        last_filter = KshootFilter.PEAK
        current_timesig = self.events_timesig()[Timing(1, 1, 0)]
        line_num = 0

        measure_iter = range(self.end.measure)
        if progress_bar:
            from tqdm import tqdm
            measure_iter = tqdm(measure_iter, unit='measure', leave=False)

        for m in measure_iter:
            measure = m + 1

            # Laser range resets every measure in ksh.
            laser_range = {LaserSide.LEFT: 1, LaserSide.RIGHT: 1}

            now = Timing(measure, 1, 0)

            if now in self.events_timesig():
                current_timesig = self.events_timesig()[now]
                print(f'beat={current_timesig.top}/{current_timesig.bottom}', file=file)

            for b in range(current_timesig.top):
                # Vox beats are also 1-indexed.
                beat = b + 1

                for o in range(TICKS_PER_BEAT):
                    line_num += 1

                    # However, vox offsets are 0-indexed.

                    now = Timing(measure, beat, o)

                    buffer = KshLineBuf()

                    if now.beat > 1 and now.offset > 0 and now in self.events_timesig():
                        raise KshConvertError(self.voxfile.name, file.name, line_num, 'time signature change in the middle of a measure')

                    if now in self.events_bpm():
                        buffer.meta.append(f't={str(self.events_bpm()[now]).rstrip("0").rstrip(".").strip()}')

                    # Camera events.
                    for cam_param in list(CameraParam):
                        if cam_param.scaling_factor() is not None and now in self.events_spcontroller(cam_param):
                            the_change = self.events_spcontroller(cam_param)[now]
                            buffer.meta.append(f'{cam_param.to_ksh_name()}={int(the_change * cam_param.scaling_factor())}')

                    if now in self.events_tiltmode():
                        buffer.meta.append(f'tilt={self.events_tiltmode()[now].to_ksh_name()}')

                    for i in list(Button):
                        if i in holds:
                            holds[i] -= 1
                            if holds[i] == 0:
                                del holds[i]
                            else:
                                buffer.buttons[i] = KshLineBuf.ButtonState.HOLD

                        if now in self.events_track(i.to_track_num()):
                            press: ButtonPress = self.events_track(i.to_track_num())[now]
                            if press.duration != 0 and i.is_fx():
                                letter = 'l' if i == Button.FX_L else 'r'
                                effect_string = f'{self.effect_defines[press.effect].fx_change(press.effect)}' if type(press.effect) is int else \
                                    press.effect[0].to_ksh_name(press.effect[1])
                                buffer.meta.append(f'fx-{letter}={effect_string}')

                            if press.duration != 0:
                                buffer.buttons[i] = KshLineBuf.ButtonState.HOLD
                                holds[i] = press.duration
                            else:
                                buffer.buttons[i] = KshLineBuf.ButtonState.PRESS

                    for side in list(LaserSide):
                        if now in self.events_track(side.to_track_num()) and side in slam_status:
                            raise KshConvertError(self.voxfile.name, file.name, line_num, 'new laser node spawn while trying to resolve slam')
                        elif now in self.events_track(side.to_track_num()):
                            laser = self.events_track(side.to_track_num())[now]
                        elif side in slam_status:
                            if slam_status[side][1] == 0:
                                laser = slam_status[side][0].end
                                del slam_status[side]
                            else:
                                buffer.lasers[side] = ':'
                                slam_status[side][1] -= 1
                                continue
                        else:
                            if lasers[side]:
                                buffer.lasers[side] = ':'
                            else:
                                buffer.lasers[side] = '-'
                            continue

                        if type(laser) is LaserSlam:
                            slam_status[side] = [laser, 3]
                            laser = laser.start

                        if laser.range != laser_range[side]:
                            buffer.meta.append(f'laserrange_{side.to_letter()}={laser.range}x')
                            laser_range[side] = laser.range

                        if laser.filter != last_filter:
                            buffer.meta.append(f'filtertype={laser.filter.to_ksh_name()}')
                            last_filter = laser.filter

                        if laser.node_type == LaserCont.START:
                            lasers[side] = True
                        elif laser.node_type == LaserCont.END:
                            lasers[side] = False
                        buffer.lasers[side] = laser.position_ksh()

                    print(buffer.out(), file=file)

            print('--', file=file)

        for k, v in self.effect_defines.items():
            print(v.to_define_line(k), file=file)

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

        return parser

    def parse(self):
        line_no = 1
        section_line_no = 0
        for line in self.voxfile:
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
                    raise VoxParseError(self.voxfile, str(self.state), line_no, f'define line "{line}" does not have 3 operands')

                self.vox_defines[splitted[1]] = int(splitted[2])
                if int(splitted[2]) != 0:
                    self.effect_defines[int(splitted[2])] = KshEffectDefine.from_pre_v4_vox_sound_id(int(splitted[2]))
            elif self.state is not None:
                try:
                    self.process_state(line, line_no, section_line_no)
                except VoxParseError as e:
                    print(f'Warning: {str(e)}')
                    self.warnings += 1
                    write_to_exceptions(vox.voxfile.name, line_no=line_no, is_input_error=True)

            section_line_no += 1
            line_no += 1
        self.finalized = True

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
]

CASES = {
    'basic': 'data/vox_08_ifs/004_0781_alice_maestera_alstroemeria_records_5m.vox',
    'laser-range': 'data/vox_12_ifs/004_1138_newleaf_blackyooh_3e.vox',
    'time-signature': 'data/vox_01_ifs/001_0056_amanojaku_164_4i.vox',
    'early-version': 'data/vox_01_ifs/001_0001_albida_muryoku_1n.vox',
    'bpm': 'data/vox_03_ifs/002_0262_hanakagerou_minamotoya_1n.vox',
    'encoding': 'data/vox_02_ifs/001_0121_eclair_au_chocolat_kamome_1n.vox',
    'camera': 'data/vox_03_ifs/002_0250_crack_traxxxx_lite_show_magic_4i.vox',
    'diff-preview': 'data/vox_01_ifs/001_0026_gorilla_pinocchio_4i.vox',
    'slam-range': 'data/vox_06_ifs/003_0529_fks_nizikawa_3e.vox',
    'new-fx': 'data/vox_01_ifs/001_0001_albida_muryoku_4i.vox',
    'bug-fx': 'data/vox_13_ifs/004_1208_coldapse_aoi_3e.vox',
    'double-fx': 'data/vox_12_ifs/004_1136_freedomdive_xi_2a.vox',
    'tilt-mode': 'data/vox_01_ifs/001_0034_phychopas_yucha_4i.vox',
    'wtf': 'data/vox_14_ifs/004_1361_feelsseasickness_kameria_5m.vox'
}

def copy_preview(vox, song_dir):
    output_path = f'{song_dir}/preview{AUDIO_EXTENSION}'
    preview_path = f'{args.preview_dir}/{vox.song_id}{AUDIO_EXTENSION}'
    diff_preview_path = f'{splitx(preview_path)[0]}_{vox.diff_token()}{AUDIO_EXTENSION}'

    if os.path.exists(diff_preview_path):
        preview_path = diff_preview_path
        output_path = f'{splitx(output_path)[0]}_{vox.diff_token()}{splitx(output_path)[1]}'

    if os.path.exists(output_path):
        print(f'> Preview file "{output_path}" already exists.')
        return

    if os.path.exists(preview_path):
        print(f'> Copying preview to "{output_path}".')
        copyfile(preview_path, output_path)
    else:
        print('> No preview file found.')

    return os.path.basename(output_path)

argparser = argparse.ArgumentParser(description='Convert vox to ksh')
argparser.add_argument('-t', '--testcase')
argparser.add_argument('-m', '--metadata-only', action='store_true')
argparser.add_argument('-p', '--preview-only', action='store_true')
argparser.add_argument('-A', '--audio-dir', default='D:/SDVX-Extract/song')
argparser.add_argument('-J', '--jacket-dir', default='D:/SDVX-Extract/jacket')
argparser.add_argument('-P', '--preview-dir', default='D:/SDVX-Extract/preview')
argparser.add_argument('-n', '--no-media', action='store_true')
argparser.add_argument('-c', '--convert', action='store_true')
args = argparser.parse_args()

AUDIO_EXTENSION = '.ogg'
EXCEPTIONS_FILE = 'exceptions.txt'
ID_TO_AUDIO = {}

if not args.no_media:
    print('Generating audio file mapping...')
    # Audio files should have a name starting with the ID followed by a space.
    for _, _, files in os.walk(args.audio_dir):
        for f in files:
            if os.path.basename(f) == 'jk':
                continue
            try:
                if splitx(f)[1] == '.ogg':
                    ID_TO_AUDIO[int(splitx(os.path.basename(f))[0])] = f
            except ValueError as e:
                print(e)
    print(f'{len(ID_TO_AUDIO)} songs processed.')

if args.testcase:
    if not args.testcase in CASES:
        print('please specify a valid testcase', file=sys.stderr)
        print('valid testcases are:', file=sys.stderr)
        for c in CASES.keys():
            print('\t' + c, file=sys.stderr)
        exit(1)

if args.convert:
    # Create output directory.
    if not os.path.exists('out'):
        print(f'Creating output directory.')
        os.mkdir('out')

    VOX_ROOT = 'data'

    exceptions_file = open(EXCEPTIONS_FILE, 'w')

    # Load source directory.
    for d in filter(lambda x: os.path.isdir(VOX_ROOT + '/' + x), os.listdir(VOX_ROOT)):
        vox_dir = VOX_ROOT + '/' + d
        for f in os.listdir(vox_dir):
            vox_path = f'{vox_dir}/{f}'
            if args.testcase and vox_path != CASES[args.testcase]:
                continue
            print(vox_path + ':')
            try:
                vox = Vox.from_file(vox_path)
            except Exception as e:
                print(f'Loading vox file failed with "{str(e)}"\nTraceback:\n{traceback.format_exc()}\n')
                write_to_exceptions(vox_path, is_critical=True)
                continue

            try:
                print(f'> Processing "{vox_path}": "{str(vox)}"')
            except (AttributeError, LookupError):
                # Metadata not found.
                print(f'Metadata issue encountered')
                write_to_exceptions(vox_path, is_critical=True)
                continue

            # First try to parse the file.
            try:
                vox.parse()
            except VoxParseError as e:
                print(f'Parsing vox file failed on line {e.line} with "{str(e)}":\n{traceback.format_exc()}')
                write_to_exceptions(vox.voxfile.name, line_no=e.line, is_input_error=True, is_critical=True)
                continue
            except Exception as e:
                print(f'Parsing vox file failed with "{str(e)}":\n{traceback.format_exc()}')
                write_to_exceptions(vox_path, is_input_error=True, is_critical=True)
                continue

            song_dir = f'out/{vox.get_metadata("ascii")}'
            if not os.path.isdir(song_dir):
                print(f'> Creating song directory "{song_dir}".')
                os.mkdir(song_dir)

            preview_basename = copy_preview(vox, song_dir)
            if args.preview_only:
                continue

            fallback_jacket_diff_idx = None

            using_difficulty_audio = False

            if not args.no_media:

                target_audio_path = song_dir + '/track.ogg'

                src_audio_path = args.audio_dir + '/' + ID_TO_AUDIO[vox.song_id]

                if vox.difficulty == Difficulty.INFINITE:
                    src_audio_path_diff = f'{splitx(src_audio_path)[0]} [INF]{splitx(src_audio_path)[1]}'
                    if os.path.exists(src_audio_path_diff):
                        print(f'> Found difficulty-specific audio "{src_audio_path_diff}".')
                        src_audio_path = src_audio_path_diff
                        target_audio_path = f'{splitx(target_audio_path)[0]}_inf{splitx(target_audio_path)[1]}'
                        using_difficulty_audio = True

                if not os.path.exists(target_audio_path):
                    print(f'> Copying audio file {src_audio_path} to song directory.')
                    copyfile(src_audio_path, target_audio_path)
                else:
                    print(f'> Audio file "{target_audio_path}" already exists.')

                src_jacket_basename = f'jk_{str(vox.game_id).zfill(3)}_{str(vox.song_id).zfill(4)}_{vox.difficulty.to_jacket_ifs_numer()}_b'
                src_jacket_path = args.jacket_dir + '/' + src_jacket_basename + '_ifs/tex/' + src_jacket_basename + '.png'

                if os.path.exists(src_jacket_path):
                    target_jacket_path = f'{song_dir}/{str(vox.difficulty.to_jacket_ifs_numer())}.png'
                    print(f'> Jacket image file found at "{src_jacket_path}". Copying to "{target_jacket_path}".')
                    copyfile(src_jacket_path, target_jacket_path)
                else:
                    print(f'> Could not find jacket image file. Checking easier diffs.')
                    fallback_jacket_diff_idx = vox.difficulty.to_jacket_ifs_numer() - 1
                    while True:
                        if fallback_jacket_diff_idx < 0:
                            print('> No jackets found for easier difficulties either. Leaving jacket blank.')
                            fallback_jacket_diff_idx = ''
                            break

                        easier_jacket_path = f'{song_dir}/{fallback_jacket_diff_idx}.png'
                        if os.path.exists(easier_jacket_path):
                            # We found the diff number with the jacket.
                            print(f'> Using jacket "{easier_jacket_path}".')
                            break
                        fallback_jacket_diff_idx -= 1

            chart_path = f'{song_dir}/{vox.difficulty.to_xml_name()}.ksh'
            print(f'> Writing KSH data to "{chart_path}".')
            with open(chart_path, "w+", encoding='utf-8') as ksh_file:
                try:
                    vox.write_to_ksh(file=ksh_file,
                               jacket_idx=str(fallback_jacket_diff_idx) if fallback_jacket_diff_idx is not None else None,
                               track_basename='track_inf.mp3' if using_difficulty_audio else None,
                               preview_basename=preview_basename)
                except KshConvertError as e:
                    print(f'Outputting to ksh failed with "{str(e)}"\n{traceback.format_exc()}\n')
                    write_to_exceptions(e.outfile, line_no=e.line, is_critical=True)
                    continue
                except Exception as e:
                    print(f'Outputting to ksh failed with "{str(e)}"\n{traceback.format_exc()}\n')
                    write_to_exceptions(vox.voxfile.name, is_critical=True)
                    continue
                print('> Success!')

    exceptions_file.close()
    exit(0)

print('Please specify something to do.')
exit(1)
