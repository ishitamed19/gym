from cgitb import reset
import sys
import gym

display = None
if sys.platform.startswith('linux'):
    print('Setting up virtual display')

    import pyvirtualdisplay
    display = pyvirtualdisplay.Display(visible=0, size=(1400, 900), color_depth=24)
    display.start()

env = gym.make('CarRacing-Adversarial-v2', birdseye=True)
env.reset()
env.reset_random()
# env.reset_agent()

for _ in range(10000):
    env.render(mode="human")
    action = env.action_space.sample()
    observation, reward, done, info = env.step(action)
    print(reward, done)
    # print(info)
    if done or (_%200==0):
        print("reset................")
        env.reset_agent()
        # try:
        #     env.reset_agent()
        # except:
        #     print("trying reset() instead of reset_random()")
        #     env.reset()
env.close()

print("DONE!")

if display:
     display.stop()
