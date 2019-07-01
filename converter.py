from enum import Enum, auto
from collections import namedtuple
from recordclass import dataobject
import random
import math

import sys
import argparse

TICKS_PER_BEAT = 48

TimeSignature = namedtuple('TimeSignature', 'top bottom')

class Button(Enum):
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
        else:
            raise ValueError('Invalid track number for button: {}'.format(num))

    def is_fx(self):
        return self == Button.FX_L or self == Button.FX_R

    BT_A = auto()
    BT_B = auto()
    BT_C = auto()
    BT_D = auto()
    FX_L = auto()
    FX_R = auto()

class KshootEffect(Enum):
    def to_ksh_name(self):
        if self == KshootEffect.RETRIGGER:
            return 'Retrigger;8'
        elif self == KshootEffect.GATE:
            return 'Gate;8'
        elif self == KshootEffect.FLANGER:
            return 'Flanger'
        elif self == KshootEffect.BITCRUSHER:
            return 'BitCrusher;10'
        elif self == KshootEffect.PHASER:
            return 'Phaser'
        elif self == KshootEffect.WOBBLE:
            return 'Wobble;12'
        elif self == KshootEffect.PITCHSHIFT:
            return 'PitchShift;12'
        elif self == KshootEffect.TAPESTOP:
            return 'TapeStop;50'
        elif self == KshootEffect.ECHO:
            return 'Echo;4;60'
        elif self == KshootEffect.SIDECHAIN:
            return 'SideChain'

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

class Timing:
    def __init__(self, measure, beat, offset):
        self.measure = measure
        self.beat = beat
        self.offset = offset

    @classmethod
    def from_time_str(cls, time: str):
        splitted = time.split(',')
        if int(splitted[2]) >= TICKS_PER_BEAT:
            raise ValueError('Offset greater than maximum')
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

class ButtonPress:
    def __init__(self, time: Timing, button: Button, duration: int):
        self.time = time
        self.button = button
        self.duration = duration

class LaserSide(Enum):
    def as_letter(self):
        return 'l' if self == LaserSide.LEFT else 'r'

    LEFT = auto()
    RIGHT = auto()

class LaserCont(Enum):
    """ The continuity status of a laser node. """
    CONTINUE = 0
    START = 1
    END = 2

class LaserNode:
    class Builder(dataobject):
        time: Timing = None
        side: LaserSide = None
        position: int = None
        node_type: LaserCont = None
        range: int = 1

    def __init__(self, builder: Builder):
        self.time = builder.time
        self.side = builder.side
        self.position = builder.position
        self.node_type = builder.node_type
        self.range = builder.range

    @staticmethod
    def kshpos(pos):
        chars = []
        for c in range(10):
            chars.append(chr(ord('0') + c))
        for c in range(24):
            chars.append(chr(ord('A') + c))
        for c in range(15):
            chars.append(chr(ord('a') + c))
        idx = math.floor((pos / 127) * (len(chars) - 1))
        return chars[idx]

class LaserSlam:
    def __init__(self, start: LaserNode, end: LaserNode):
        if start.time != end.time:
            raise ValueError('start time {} differs from end time {}'.format(start.time, end.time))
        self.start = start
        self.end = end
        self.time = start.time

LaserRangeChange = namedtuple('LaserRangeChange', 'time side range')

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
            elif token == 'BEAT INFO':
                return cls.BEAT_INFO
            elif token == 'END POSISION' or token == 'END POSITION':
                return cls.END_POSITION
            elif token == 'SOUND ID START':
                return cls.SOUND_ID
            elif token == 'TRACK AUTO TAB':
                return None
            elif token.startswith('TRACK'):
                return cls.TRACK, int(token[5])

        NONE = auto()
        FORMAT_VERSION = auto()
        BPM = auto()
        BPM_INFO = auto()
        BEAT_INFO = auto()
        END_POSITION = auto()
        SOUND_ID = auto()
        TRACK = auto()

    def __init__(self):
        self.time_sigs = {}
        self.bpms = {}
        self.end = None
        self.events = []

        self.state = None
        self.state_track = 0

    def process_state(self, line):
        splitted = line.split('\t')

        if self.state == self.State.BEAT_INFO:
            timesig = TimeSignature(splitted[1], splitted[2])
            self.time_sigs[Timing.from_time_str(splitted[0])] = timesig
        elif self.state == self.State.BPM:
            self.bpms[Timing(1, 1, 0)] = float(line)
        elif self.state == self.State.BPM_INFO:
            self.bpms[Timing.from_time_str(splitted[0])] = splitted[1]
            # TODO There's at least a third column.
        elif self.state == self.State.END_POSITION:
            self.end = Timing.from_time_str(line)
        elif self.state == self.State.SOUND_ID:
            # TODO Figure this out.
            pass
        elif self.state == self.state.TRACK:
            if self.state_track == 1 or self.state_track == 8:
                laser_node = LaserNode.Builder()
                laser_node.time = Timing.from_time_str(splitted[0])
                laser_node.side = LaserSide.LEFT if self.state_track == 1 else LaserSide.RIGHT
                laser_node.position = int(splitted[1])
                laser_node.node_type = LaserCont(int(splitted[2]))
                if splitted[5]:
                    laser_node.range = int(splitted[5])
                laser_node = LaserNode(laser_node)

                if laser_node.position > 127 or laser_node.position < 0:
                    raise ValueError('laser position out of bounds: {}'.format(laser_node.position))

                # Check if it's a slam.
                slam_start = None
                for e in self.events:
                    if type(e) is LaserNode and e.side == laser_node.side and e.time == laser_node.time:
                        # We're gonna remove the laser node and replace  it with a slam node.
                        slam_start = e
                        break
                if slam_start is None:
                    self.events.append(laser_node)
                else:
                    slam = LaserSlam(slam_start, laser_node)
                    self.events.remove(slam_start)
                    self.events.append(slam)
            else:
                button = Button.from_track_num(self.state_track)
                self.events.append(ButtonPress(Timing.from_time_str(splitted[0]), button, int(splitted[1])))

    def signature_at_time(self, time):
        # TODO
        return TimeSignature(4, 4)

    def as_ksh(self, file=sys.stdout):
        print('''title=
artist=
effect=
jacket=.jpg
illustrator=
difficulty=extended
level=1
t=140
m=test.mp3
mvol=75
o=0
bg=desert
layer=arrow
po=0
plength=15000
pfiltergain=50
filtertype=peak
chokkakuautovol=0
chokkakuvol=50
ver=167
--
beat=4/4''')
        # Holds come in the pair <button>,<duration>
        holds = []
        lasers = {LaserSide.LEFT: False, LaserSide.RIGHT: False}
        slams = []
        for m in range(self.end.measure):
            measure = m + 1

            # Laser range resets every measure with KSH
            laser_range = {LaserSide.LEFT: 1, LaserSide.RIGHT: 1}
            for b in range(self.signature_at_time(None).top):
                beat = b + 1
                for o in range(TICKS_PER_BEAT):
                    buffer = ''

                    buttons_here = []
                    lasers_here = {LaserSide.LEFT: None, LaserSide.RIGHT: None}
                    for e in filter(lambda e: e.time == Timing(measure, beat, o), self.events):
                        # Check if it's a hold first.
                        if type(e) is ButtonPress and e.duration != 0:
                            if Button.is_fx(e.button):
                                # Assign random FX to FX hold.
                                buffer += 'fx-{}={}\n'.format('l' if e.button == Button.FX_L else 'r', random.choice(list(KshootEffect)).to_ksh_name())
                            holds.append([e.button, e.duration])
                        elif type(e) is ButtonPress:
                            buttons_here.append(e.button)
                        elif type(e) is LaserNode:
                            if e.range != laser_range[e.side]:
                                buffer += 'laserrange_{}={}x\n'.format(e.side.as_letter(), e.range)
                                laser_range[e.side] = e.range

                            lasers_here[e.side] = e
                            if e.node_type == LaserCont.START:
                                lasers[e.side] = True
                            elif e.node_type == LaserCont.END:
                                lasers[e.side] = False
                        elif type(e) is LaserSlam:
                            lasers_here[e.start.side] = e.start
                            # Slam tuple: <slam>,<ticks since slam start>
                            slams.append([e, 0])

                    # Print button state.
                    for b in [
                        Button.BT_A,
                        Button.BT_B,
                        Button.BT_C,
                        Button.BT_D,
                        Button.FX_L,
                        Button.FX_R
                    ]:
                        yes = '1'
                        no = '0'
                        hold = '2'

                        if b == Button.FX_L or b == Button.FX_R:
                            yes = '2'
                            hold = '1'

                        if b == Button.FX_L:
                            buffer += '|'

                        if b in buttons_here:
                            buffer += yes
                        elif b in map(lambda h: h[0], holds):
                            buffer += hold
                        else:
                            buffer += no

                    buffer += '|'

                    # Now print laser state.
                    for l in [
                        LaserSide.LEFT,
                        LaserSide.RIGHT
                    ]:
                        slam = None
                        laser_cancel = False
                        for s in slams:
                            if s[0].start.side == l and (s[0].time == Timing(measure, beat, o) or s[1]):
                                slam = s
                                break
                        if slam is not None:
                            if slam[1] == 3:
                                buffer += LaserNode.kshpos(slam[0].end.position)
                                slams.remove(slam)
                                if slam[0].end.node_type == LaserCont.END:
                                    lasers[l] = False
                                    laser_cancel = True
                            else:
                                lasers[l] = True
                                slam[1] += 1

                        if lasers_here[l] is not None:
                            buffer += LaserNode.kshpos(lasers_here[l].position)
                        else:
                            if lasers[l]:
                                buffer += ':'
                            elif not laser_cancel:
                                # No laser event or ongoing laser.
                                buffer += '-'

                    print(buffer, file=file)

                    # Subtract time remaining from holds.
                    for h in holds:
                        h[1] -= 1
                    holds = list(filter(lambda h: h[1] > 0, holds))

            print('--', file=file)


    @classmethod
    def from_file(cls, path):
        parser = Vox()

        file = open(path, 'r')

        line_no = 1
        for line in file:
            if line_no >= 1000:
                break
            line = line.strip()
            if line.startswith('//'):
                continue
            if line.startswith('#'):
                token_state = cls.State.from_token(line.split('#')[1])
                if token_state is None:
                    continue
                if type(token_state) is tuple:
                    parser.state = token_state[0]
                    parser.state_track = int(token_state[1])
                else:
                    parser.state = token_state
            elif parser.state is not None:
                parser.process_state(line)
            line_no += 1

        return parser

CASES = {
    'basic': 'data/vox_08_ifs/004_0781_alice_maestera_alstroemeria_records_5m.vox',
    'laser-range': 'data/vox_12_ifs/004_1138_newleaf_blackyooh_3e.vox'
}

parser = argparse.ArgumentParser(description='Convert vox to ksh')
parser.add_argument('-t', '--testcase')
args = parser.parse_args()

if args.testcase:
    if not CASES[args.testcase]:
        print('please specify a valid testcase', file=sys.stderr)
        print('valid testcases are:', file=sys.stderr)
        for c in CASES.keys():
            print('\t' + c)
    vox = Vox.from_file(CASES[args.testcase])
    vox.as_ksh()