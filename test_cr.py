import sys
import gym

display = None
if sys.platform.startswith('linux'):
    print('Setting up virtual display')

    import pyvirtualdisplay
    display = pyvirtualdisplay.Display(visible=0, size=(1400, 900), color_depth=24)
    display.start()

env = gym.make('CarRacing-v2', birdseye=True)

try:
    env.reset_random()
except:
    print("trying reset() instead of reset_random()")
    env.reset()
for _ in range(1000):
    env.render()
    action = env.action_space.sample()
    observation, reward, done, info = env.step(action)
    if done or (_%200==0):
        try:
            env.reset_random()
        except:
            print("trying reset() instead of reset_random()")
            env.reset()
env.close()

print("DONE!")

if display:
     display.stop()
