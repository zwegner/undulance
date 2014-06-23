import imp
import struct
import subprocess
import sys
import threading
import traceback

import audio

ctx = audio.Context()
ctx.sample_rate = 44100

if 0:
    import pyaudio
    p = pyaudio.PyAudio()
    #print(p.get_device_count())
    #for i in range(p.get_device_count()):
    #    print(p.get_device_info_by_index(i))

    stream = p.open(format=pyaudio.paInt16,
            output_device_index=1,
            channels=1,
            rate=ctx.sample_rate,
            output=True)
else:
    p = subprocess.Popen(['sox', '-q', '-r', '44100', '-b', '16', '-e',
            'signed-integer', '-c', '1', '-t', 'raw', '--buffer', '128', '-', '-d'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    stream = p.stdin

class InputThread(threading.Thread):
    def run(self):
        global last_audio
        while True:
            try:
                input('> ')
                last_audio = audio
                imp.reload(audio)
                audio.sample_rate = ctx.sample_rate
            except EOFError:
                sys.exit()
            except:
                traceback.print_exc()
                continue

tr = InputThread()
tr.daemon=True
tr.start()

ctx.sample = 0
last_audio = audio
while True:
    try:
        ctx.sample += 1
        sample = audio.eq.eval(ctx)
        stream.write(struct.pack('h', int(sample * (65535 / 20.))))
        stream.flush()
    except KeyboardInterrupt:
        sys.exit()
    except:
        if audio == last_audio:
            raise
        audio = last_audio
