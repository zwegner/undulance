import math
import os
import pyaudio
import random
import readline
import struct
import subprocess
import sys
import threading

sample_rate = 44100
p = pyaudio.PyAudio()
#    print(p.get_device_count())
#    for i in range(p.get_device_count()):
#        print(p.get_device_info_by_index(i))

# open stream
stream = p.open(format=pyaudio.paFloat32,
        output_device_index=2,
        channels=1,
        rate=sample_rate,
        output=True)

def exit():
    sys.exit(0)

class Context:
    pass

def fixup(arg):
    if isinstance(arg, list):
        return [fixup(a) for a in arg]
    if isinstance(arg, (float, int)):
        arg = Const(arg)
    return arg

def operator(*params):
    def decorate(cls):
        def __init__(self, *args):
            for p, a in zip(params, args):
                # Is this ever not needed?
                if not isinstance(self, Const):
                    a = fixup(a)
                setattr(self, p, a)
            if hasattr(self, 'setup'):
                self.setup()
        cls.__init__ = __init__
        if hasattr(cls, '__str__'):
            def __str__(self):
                return '%s(%s)' % (self.__class__.__name__,
                        ', '.join(str(getattr(self, p)) for p in params))
            cls.__str__ = __str__
        return cls
    return decorate

class Node:
    pass

@operator('value')
class Const(Node):
    def eval(self, ctx):
        return self.value
    def __str__(self):
        return '%s' % self.value

@operator('lhs', 'rhs')
class Binop(Node):
    def __str__(self):
        return '(%s %s %s)' % (self.lhs, self.__class__.op, self.rhs)
    def walk_tree(self):
        yield self
        for c in [self.lhs, self.rhs]:
            for n in c.walk_tree():
                yield n

all_ops = []
def binop(op):
    def deco(x):
        all_ops.append(x)
        x.op = op
        return x
    return deco

@binop('+')
class Add(Binop):
    def eval(self, ctx):
        return self.lhs.eval(ctx) + self.rhs.eval(ctx)

@binop('-')
class Sub(Binop):
    def eval(self, ctx):
        return self.lhs.eval(ctx) - self.rhs.eval(ctx)

@binop('*')
class Mul(Binop):
    def eval(self, ctx):
        return self.lhs.eval(ctx) * self.rhs.eval(ctx)

@binop('/')
class Div(Binop):
    def eval(self, ctx):
        return self.lhs.eval(ctx) / self.rhs.eval(ctx)

# Add in operator overloading to Node class. Must be done after the child classes
# are instantiated... weird.

ops = {
    '__add__': Add,
    '__sub__': Sub,
    '__mul__': Mul,
    '__truediv__': Div,
}
for name, cls in ops.items():
    # ugh, make sure there's a new scope so the right names get captured
    def add(name, cls):
        def blah(self, rhs):
            return cls(self, rhs)
        setattr(Node, name, blah)
    add(name, cls)

@operator('freq')
class Osc(Node):
    def setup(self):
        self.phase = 0
    def eval(self, ctx):
        self.phase += self.freq.eval(ctx) / ctx.sample_rate
        return self.eval_wave(self.phase % 1)
    def __str__(self):
        return '%s(%s)' % (self.__class__.__name__, self.freq)

class Sine(Osc):
    def eval_wave(self, phase):
        return math.sin(phase * 2 * math.pi)

class Square(Osc):
    def eval_wave(self, phase):
        return 1 if phase > 0.5 else -1

@operator('freq', 'pulse_width')
class Pulse(Osc):
    def eval_wave(self, phase):
        return 1 if phase > self.pulse_width.eval(ctx) else -1

class SawUp(Osc):
    def eval_wave(self, phase):
        return 2 * phase - 1

class SawDown(Osc):
    def eval_wave(self, phase):
        return -2 * phase + 1

class Tri(Osc):
    def eval_wave(self, phase):
        return 4 * phase - 1 if phase < 0.5 else -4 * phase + 3

@operator()
class Noise(Node):
    def eval(self, ctx):
        return 2 * random.random() - 1

@operator('input', 'gate')
class Envelope(Node):
    def setup(self):
        self.last_gate = 0
        self.current = 0
    def eval(self, ctx):
        gate = self.gate.eval(ctx) > 0
        trigger = gate and not self.last_gate
        self.last_gate = gate
        #if not gate:
        #    self.current = 0
        if trigger:
            self.current = 1
        self.current *= .9999
        return self.current * self.input.eval(ctx)

def EnvelopeBeat(input, beat):
    return Envelope(input, Trigger(beat))

@operator('note')
class Diatonic(Node):
    half_step = 2 ** (1 / 12)
    def eval(self, ctx):
        return 256 * Diatonic.half_step ** (self.note.eval(ctx) - 40)

@operator('bpm')
class Beat(Node):
    def eval(self, ctx):
        return self.bpm.eval(ctx) * (ctx.sample / (ctx.sample_rate * 60))

@operator('beat')
class Trigger(Node):
    def setup(self):
        self.last_beat = -1
    def eval(self, ctx):
        beat = int(self.beat.eval(ctx))
        trigger = beat != self.last_beat
        self.last_beat = beat
        return trigger

@operator('beat', 'args')
class Switcher(Node):
    def eval(self, ctx):
        return self.args[int(self.beat.eval(ctx)) % len(self.args)].eval(ctx)

#class Historic:

ctx = Context()
ctx.sample_rate = sample_rate

beat = Beat(120)
section = Switcher(beat / 8, [1, 2, 1, 4])
eq = EnvelopeBeat(SawUp(Diatonic(section + Switcher(beat, [30]))), beat * section)
eq += EnvelopeBeat(SawUp(110), beat / 2)
eq += EnvelopeBeat(SawDown(256), beat / 4)
eq /= 20

ctx.sample = 0
while True:
    try:
        ctx.sample += 1
        sample = eq.eval(ctx)
        stream.write(struct.pack('f', sample))
    except KeyboardInterrupt:
        exit()
