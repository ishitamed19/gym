__credits__ = ["Andrea PIERRÉ"]

import math
from typing import Optional, Union

import numpy as np

import gym
from gym import spaces
from gym.envs.box2d.car_dynamics import Car
from gym.error import DependencyNotInstalled, InvalidAction
from gym.utils import seeding, EzPickle
from gym.utils.renderer import Renderer
from gym.utils import geo_complexity

try:
    import Box2D
    from Box2D.b2 import contactListener, fixtureDef, polygonShape
except ImportError:
    raise DependencyNotInstalled("box2D is not installed, run `pip install gym[box2d]`")

try:
    # As pygame is necessary for using the environment (reset and step) even without a render mode
    #   therefore, pygame is a necessary import for the environment.
    import pygame
    from pygame import gfxdraw
except ImportError:
    raise DependencyNotInstalled(
        "pygame is not installed, run `pip install gym[box2d]`"
    )

from . import bezier
from . import racetracks

STATE_W = 96  # less than Atari 160x192
STATE_H = 96
VIDEO_W = 600
VIDEO_H = 400
WINDOW_W = 1000
WINDOW_H = 800

SCALE = 6.0  # Track scale
TRACK_RAD = 900 / SCALE  # Track is heavily morphed circle with this radius
PLAYFIELD = 2000 / SCALE  # Game over boundary
FPS = 50  # Frames per second
ZOOM = 2.7  # Camera zoom
ZOOM_FOLLOW = True  # Set to False for fixed view (don't use zoom)


TRACK_DETAIL_STEP = 21 / SCALE
TRACK_TURN_RATE = 0.31
TRACK_WIDTH = 40 / SCALE
BORDER = 8 / SCALE
BORDER_MIN_COUNT = 4
GRASS_DIM = PLAYFIELD / 20.0
MAX_SHAPE_DIM = (
    max(GRASS_DIM, TRACK_WIDTH, TRACK_DETAIL_STEP) * math.sqrt(2) * ZOOM * SCALE
)


class FrictionDetector(contactListener):
    def __init__(self, env, lap_complete_percent):
        contactListener.__init__(self)
        self.env = env
        self.lap_complete_percent = lap_complete_percent

    def BeginContact(self, contact):
        self._contact(contact, True)

    def EndContact(self, contact):
        self._contact(contact, False)

    def _contact(self, contact, begin):
        tile = None
        obj = None
        u1 = contact.fixtureA.body.userData
        u2 = contact.fixtureB.body.userData
        index = -1
        if u1 and "tile" in u1:
            if "road_friction" in u1['tile'].__dict__:
                tile = u1['tile']
                index = u1['index']
                obj = u2
        if u2 and "tile" in u2:
            if "road_friction" in u2['tile'].__dict__:
                tile = u2['tile']
                index = u2['index']
                obj = u1
        if not tile:
            return

        # inherit tile color from env
        tile.color = self.env.road_color / 255
        if not obj or "tiles" not in obj.__dict__:
            return
        if begin:
            obj.tiles.add(tile)
            if not tile.road_visited:
                tile.road_visited = True
                self.env.reward += 1000.0 / len(self.env.track)
                self.env.tile_visited_count += 1

                # Lap is considered completed if enough % of the track was covered
                if (
                    tile.idx == 0
                    and self.env.tile_visited_count / len(self.env.track)
                    > self.lap_complete_percent
                ):
                    self.env.new_lap = True

            if self.env.sparse_rewards and index >= 0:
                self._eval_tile_index(index)
        else:
            obj.tiles.remove(tile)

    def _eval_tile_index(self, index):
        goal_bin = self.env.goal_bin
        track_len = len(self.env.track)
        goal_step = track_len/(self.env.num_goal_bins)
        MIN_DISTANCE_TO_GO = 10
        distance = track_len - index
        tile_bin = np.floor(distance/goal_step)
        # print('in tile bin, index', tile_bin, index, flush=True)
        if goal_bin == 0 and distance < MIN_DISTANCE_TO_GO:
            self.env.goal_reached = False
        elif goal_bin == self.env.num_goal_bins - 1 \
            and index < MIN_DISTANCE_TO_GO:
            self.env.goal_reached = False
        elif tile_bin == goal_bin:
            self.env.goal_reached = True
            # print(f'goal bin {goal_bin} reached!', flush=True)


class CarRacingBezier(gym.Env, EzPickle):
    """
    ### Description
    The easiest control task to learn from pixels - a top-down
    racing environment. The generated track is random every episode.

    Some indicators are shown at the bottom of the window along with the
    state RGB buffer. From left to right: true speed, four ABS sensors,
    steering wheel position, and gyroscope.
    To play yourself (it's rather fast for humans), type:
    ```
    python gym/envs/box2d/car_racing.py
    ```
    Remember: it's a powerful rear-wheel drive car - don't press the accelerator
    and turn at the same time.

    ### Action Space
    If continuous:
        There are 3 actions: steering (-1 is full left, +1 is full right), gas, and breaking.
    If discrete:
        There are 5 actions: do nothing, steer left, steer right, gas, brake.

    ### Observation Space
    State consists of 96x96 pixels.

    ### Rewards
    The reward is -0.1 every frame and +1000/N for every track tile visited,
    where N is the total number of tiles visited in the track. For example,
    if you have finished in 732 frames, your reward is
    1000 - 0.1*732 = 926.8 points.

    ### Starting State
    The car starts at rest in the center of the road.

    ### Episode Termination
    The episode finishes when all of the tiles are visited. The car can also go
    outside of the playfield - that is, far off the track, in which case it will
    receive -100 reward and die.

    ### Arguments
    `lap_complete_percent` dictates the percentage of tiles that must be visited by
    the agent before a lap is considered complete.

    Passing `domain_randomize=True` enables the domain randomized variant of the environment.
    In this scenario, the background and track colours are different on every reset.

    Passing `continuous=False` converts the environment to use discrete action space.
    The discrete action space has 5 actions: [do nothing, left, right, gas, brake].

    ### Reset Arguments
    Passing the option `options["randomize"] = True` will change the current colour of the environment on demand.
    Correspondingly, passing the option `options["randomize"] = False` will not change the current colour of the environment.
    `domain_randomize` must be `True` on init for this argument to work.
    Example usage:
    ```py
        env = gym.make("CarRacing-v1", domain_randomize=True)

        # normal reset, this changes the colour scheme by default
        env.reset()

        # reset with colour scheme change
        env.reset(options={"randomize": True})

        # reset with no colour scheme change
        env.reset(options={"randomize": False})
    ```

    ### Version History
    - v1: Change track completion logic and add domain randomization (0.24.0)
    - v0: Original version

    ### References
    - Chris Campbell (2014), http://www.iforce2d.net/b2dtut/top-down-car.

    ### Credits
    Created by Oleg Klimov
    """

    metadata = {
        "render_modes": [
            "human",
            "rgb_array",
            "state_pixels",
            "single_rgb_array",
            "single_state_pixels",
            "level",
            "sketch"
        ],
        "render_fps": FPS,
    }

    def __init__(
        self,
        render_mode: Optional[str] = None,
        verbose: bool = False,
        lap_complete_percent: float = 0.95,
        domain_randomize: bool = False,
        continuous: bool = True,
        birdseye: bool = False,
        n_control_points: int = 12,
        track_name: Optional[str] = None,
        bezier: bool = True, 
        show_borders: bool = True, 
        show_indicators: bool = True,
        seed: Optional[int] = None,
        fixed_environment: bool = False,
        animate_zoom: bool = False,
        min_rad_ratio: float = 0.333333333,
        max_rad_ratio: float = 1.0,
        sparse_rewards: bool = False,
        clip_reward: Optional[int] = None,
        num_goal_bins: int = 24,
    ):
        EzPickle.__init__(self)
        self.continuous = continuous
        self.domain_randomize = domain_randomize
        self._init_colors()

        self.birdseye = birdseye
        self.level_seed = seed
        self.seed(seed)
        
        self.n_control_points = n_control_points
        self.bezier = bezier
        self.fixed_environment = fixed_environment
        self.animate_zoom = animate_zoom
        self.min_rad_ratio = min_rad_ratio
        self.max_rad_ratio = max_rad_ratio
        self.steps = 0

        self.contactListener_keepref = FrictionDetector(self, lap_complete_percent)
        self.world = Box2D.b2World((0, 0), contactListener=self.contactListener_keepref)
        self.screen: Optional[pygame.Surface] = None
        self.surf = None
        self.clock = None
        self.isopen = True
        self.invisible_state_window = None
        self.invisible_video_window = None
        self.road = None
        self.car: Optional[Car] = None
        self.reward = 0.0
        self.prev_reward = 0.0
        self.verbose = verbose
        self.new_lap = False
        self.fd_tile = fixtureDef(
            shape=polygonShape(vertices=[(0, 0), (1, 0), (1, -1), (0, -1)])
        )

        self.preloaded_track = racetracks.get_track(track_name)
        self.show_borders = show_borders
        self.show_indicators = show_indicators

        self.track_data = None
        self.complexity_info = None
        self.window_h = WINDOW_H
        self.window_w = WINDOW_W
        self.track_rad = TRACK_RAD
        self.track_width = TRACK_WIDTH
        if self.preloaded_track:
            self.playfield = self.preloaded_track.bounds / SCALE
            self.full_zoom = self.preloaded_track.full_zoom
        else:
            self.playfield = PLAYFIELD
            self.full_zoom = 0.25

        # This will throw a warning in tests/envs/test_envs in utils/env_checker.py as the space is not symmetric
        #   or normalised however this is not possible here so ignore
        if self.continuous:
            self.action_space = spaces.Box(
                np.array([-1, 0, 0]).astype(np.float32),
                np.array([+1, +1, +1]).astype(np.float32),
            )  # steer, gas, brake
        else:
            self.action_space = spaces.Discrete(5)
            # do nothing, left, right, gas, brake

        self.observation_space = spaces.Box(
            low=0, high=255, shape=(STATE_H, STATE_W, 3), dtype=np.uint8
        )

        self.render_mode = render_mode
        self.renderer = Renderer(self.render_mode, self._render)

        self.clip_reward = clip_reward
        # Create goal for sparse rewards
        self.sparse_rewards = sparse_rewards
        self.num_goal_bins = num_goal_bins # 0-indexed
        self.goal_bin = None
        if sparse_rewards:
            self.set_goal()
            self.accumulated_rewards = 0.0

    def seed(self, seed=None):
        self.np_random, seed = seeding.np_random(seed)
        return [seed]

    def get_complexity_info(self):
        if self.complexity_info is None:
            # recompute
            points = ((x,y) for _,_,x,y in self.track)
            return geo_complexity.complexity(points)
        return self.complexity_info
    
    def set_goal(self, goal_bin=None):
        if goal_bin is None:
            goal_bin = self.goal_bin
        if goal_bin is None:
            self.goal_bin = self.np_random.randint(1,self.num_goal_bins)
            # self.goal_bin = 10
            # print('set goal to', self.goal_bin)
        else:
            self.goal_bin = goal_bin
        self.goal_reached = False
        # print(f'goal bin set to {self.goal_bin}', flush=True)

    def _destroy(self):
        if not self.road:
            return
        for t in self.road:
            t.userData = t.userData['tile']
            self.world.DestroyBody(t)
        self.road = []
        assert self.car is not None
        self.car.destroy()
        self.car = None

    def _init_colors(self):
        if self.domain_randomize:
            # domain randomize the bg and grass colour
            self.road_color = self.np_random.uniform(0, 210, size=3)

            self.bg_color = self.np_random.uniform(0, 210, size=3)

            self.grass_color = np.copy(self.bg_color)
            idx = self.np_random.integers(3)
            self.grass_color[idx] += 20
        else:
            # default colours
            self.road_color = np.array([102, 102, 102])
            self.bg_color = np.array([102, 204, 102])
            self.grass_color = np.array([102, 230, 102])

    def _reinit_colors(self, randomize):
        assert (
            self.domain_randomize
        ), "domain_randomize must be True to use this function."

        if randomize:
            # domain randomize the bg and grass colour
            self.road_color = self.np_random.uniform(0, 210, size=3)

            self.bg_color = self.np_random.uniform(0, 210, size=3)

            self.grass_color = np.copy(self.bg_color)
            idx = self.np_random.integers(3)
            self.grass_color[idx] += 20

    def _create_track(self, control_points=None, show_borders=None):
        if self.bezier:
            return self._create_track_bezier(
                control_points=control_points, 
                show_borders=show_borders)
        else:
            t = 0
            reset_random = False
            while True:
                t += 1
                if t > 10:
                    reset_random = True
                    break

                success = self._create_track_polar(
                    control_points=control_points,
                    show_borders=show_borders)
                if success:
                    return success

        if reset_random:
            t = 0
            while True:
                t += 1
                success = self._create_track_polar(
                    show_borders=show_borders)
                if success:
                    return success

    def _create_track_bezier(self, control_points=None, show_borders=None):
        if show_borders is None:
            show_borders = self.show_borders
        else:
            show_borders = show_borders

        # Create random bezier curve
        track = []
        self.road = []

        if self.preloaded_track is not None:
            points = self.preloaded_track.xy
            x,y = zip(*points)
        elif control_points is not None:
            a = np.array(control_points)
            x, y, _ = bezier.get_bezier_curve(a=a, rad=0.2, edgy=0.2, numpoints=40)
            self.track_data = a
        else:
            a = bezier.get_random_points(n=self.n_control_points, scale=self.playfield, np_random=self.np_random)
            x, y, _ = bezier.get_bezier_curve(a=a, rad=0.2, edgy=0.2, numpoints=40)
            self.track_data = a

        min_x, max_x = x[-1], x[-1]
        min_y, max_y = y[-1], y[-1]

        points = list(zip(x,y))
        betas = []
        for i, p in enumerate(points[:-1]):
            x1, y1 = points[i]
            x2, y2 = points[i+1]
            dx = x2 - x1
            dy = y2 - y1
            if (dx == dy == 0):
                continue

            # alpha = math.atan(dy/(dx+1e-5))
            alpha = np.arctan2(dy, dx)
            beta = math.pi/2 + alpha

            track.append((alpha, beta, x1, y1))
            betas.append(beta)

            min_x = min(x1, min_x)
            min_y = min(y1, min_y)
            max_x = max(x1, max_x)
            max_y = max(y1, max_y)

        x_offset = min_x + (max_x - min_x)/2
        y_offset = min_y + (max_y - min_y)/2
        self.x_offset = x_offset
        self.y_offset = y_offset

        betas = np.array(betas)
        abs_dbeta = abs(betas[1:] - betas[0:-1])
        mean_abs_dbeta = abs_dbeta.mean()
        std_abs_dbeta = abs_dbeta.std()
        one_dev_dbeta = mean_abs_dbeta + std_abs_dbeta/2

        # Red-white border on hard turns
        border = [False] * len(track)
        if show_borders:
            for i in range(len(track)):
                good = True
                oneside = 0
                for neg in range(BORDER_MIN_COUNT):
                    beta1 = track[i - neg - 0][1]
                    beta2 = track[i - neg - 1][1]
                    good &= abs(beta1 - beta2) > mean_abs_dbeta #TRACK_TURN_RATE * 0.2
                    oneside += np.sign(beta1 - beta2)
                good &= abs(oneside) == BORDER_MIN_COUNT
                border[i] = good
            for i in range(len(track)):
                for neg in range(BORDER_MIN_COUNT):
                    border[i - neg] |= border[i]

        # Create tiles
        for i in range(len(track)):
            alpha1, beta1, x1, y1 = track[i]
            alpha2, beta2, x2, y2 = track[i - 1]
            road1_l = (
                x1 - TRACK_WIDTH * math.cos(beta1) - x_offset,
                y1 - TRACK_WIDTH * math.sin(beta1) - y_offset,
            )
            road1_r = (
                x1 + TRACK_WIDTH * math.cos(beta1) - x_offset,
                y1 + TRACK_WIDTH * math.sin(beta1) - y_offset,
            )
            road2_l = (
                x2 - TRACK_WIDTH * math.cos(beta2) - x_offset,
                y2 - TRACK_WIDTH * math.sin(beta2) - y_offset,
            )
            road2_r = (
                x2 + TRACK_WIDTH * math.cos(beta2) - x_offset,
                y2 + TRACK_WIDTH * math.sin(beta2) - y_offset,
            )
            vertices = [road1_l, road1_r, road2_r, road2_l]
            try:
                self.fd_tile.shape.vertices = vertices
            except:
                pass
            t = self.world.CreateStaticBody(fixtures=self.fd_tile)
            t.userData = {
                'tile': t,
                'index': i
            }
            c = 0.01 * (i % 3) * 255
            t.color = self.road_color + c
            t.road_visited = False
            t.road_friction = 1.0
            t.idx = i
            t.fixtures[0].sensor = True
            self.road_poly.append(([road1_l, road1_r, road2_r, road2_l], t.color))
            self.road.append(t)
            if self.show_borders and border[i]:
                side = np.sign(beta2 - beta1)
                b1_l = (
                    x1 + side * TRACK_WIDTH * math.cos(beta1) - x_offset,
                    y1 + side * TRACK_WIDTH * math.sin(beta1) - y_offset,
                )
                b1_r = (
                    x1 + side * (TRACK_WIDTH + BORDER) * math.cos(beta1) - x_offset,
                    y1 + side * (TRACK_WIDTH + BORDER) * math.sin(beta1) - y_offset,
                )
                b2_l = (
                    x2 + side * TRACK_WIDTH * math.cos(beta2) - x_offset,
                    y2 + side * TRACK_WIDTH * math.sin(beta2) - y_offset,
                )
                b2_r = (
                    x2 + side * (TRACK_WIDTH + BORDER) * math.cos(beta2) - x_offset,
                    y2 + side * (TRACK_WIDTH + BORDER) * math.sin(beta2) - y_offset,
                )
                self.road_poly.append(
                    (
                        [b1_l, b1_r, b2_r, b2_l],
                        (255, 255, 255) if i % 2 == 0 else (255, 0, 0),
                    )
                )
        self.track = track
        self.complexity_info = geo_complexity.complexity(points)
        return True

    def _create_track_polar(self, control_points=None, show_borders=None):
        if show_borders is None:
            show_borders = self.show_borders
        else:
            show_borders = show_borders
        
        CHECKPOINTS = self.n_control_points

        self.x_offset = 0
        self.y_offset = 0

        min_rad = TRACK_RAD*self.min_rad_ratio
        max_rad = TRACK_RAD*self.max_rad_ratio

        # Create checkpoints
        if control_points is not None:
            checkpoints = control_points
            self.start_alpha = 2 * math.pi * (-0.5) / self.n_control_points
        else:
            checkpoints = []
            for c in range(CHECKPOINTS):
                noise = self.np_random.uniform(0, 2 * math.pi * 1 / CHECKPOINTS)
                alpha = 2 * math.pi * c / CHECKPOINTS + noise
                rad = self.np_random.uniform(min_rad, max_rad)

                if c == 0:
                    alpha = 0
                    rad = 1.5 * TRACK_RAD
                if c == CHECKPOINTS - 1:
                    alpha = 2 * math.pi * c / CHECKPOINTS
                    self.start_alpha = 2 * math.pi * (-0.5) / CHECKPOINTS
                    rad = 1.5 * TRACK_RAD

                checkpoints.append((alpha, rad * math.cos(alpha), rad * math.sin(alpha)))
        self.track_data = checkpoints
        self.road = []

        # Go from one checkpoint to another to create track
        # x, y, beta = 1.5 * TRACK_RAD, 0, 0
        _,x,y = checkpoints[0]
        beta = 0
        dest_i = 0
        laps = 0
        track = []
        no_freeze = 2500
        visited_other_side = False
        while True:
            alpha = math.atan2(y, x)
            if visited_other_side and alpha > 0:
                laps += 1
                visited_other_side = False
            if alpha < 0:
                visited_other_side = True
                alpha += 2 * math.pi

            while True:  # Find destination from checkpoints
                failed = True

                while True:
                    dest_alpha, dest_x, dest_y = checkpoints[dest_i % len(checkpoints)]
                    if alpha <= dest_alpha:
                        failed = False
                        break
                    dest_i += 1
                    if dest_i % len(checkpoints) == 0:
                        break

                if not failed:
                    break

                alpha -= 2 * math.pi
                continue

            r1x = math.cos(beta)
            r1y = math.sin(beta)
            p1x = -r1y
            p1y = r1x
            dest_dx = dest_x - x  # vector towards destination
            dest_dy = dest_y - y
            # destination vector projected on rad:
            proj = r1x * dest_dx + r1y * dest_dy
            while beta - alpha > 1.5 * math.pi:
                beta -= 2 * math.pi
            while beta - alpha < -1.5 * math.pi:
                beta += 2 * math.pi
            prev_beta = beta
            proj *= SCALE
            if proj > 0.3:
                beta -= min(TRACK_TURN_RATE, abs(0.001 * proj))
            if proj < -0.3:
                beta += min(TRACK_TURN_RATE, abs(0.001 * proj))
            x += p1x * TRACK_DETAIL_STEP
            y += p1y * TRACK_DETAIL_STEP
            track.append((alpha, prev_beta * 0.5 + beta * 0.5, x, y))
            if laps > 4:
                break
            no_freeze -= 1
            if no_freeze == 0:
                break

        # Find closed loop range i1..i2, first loop should be ignored, second is OK
        i1, i2 = -1, -1
        i = len(track)
        while True:
            i -= 1
            if i == 0:
                return False  # Failed
            pass_through_start = (
                track[i][0] > self.start_alpha and track[i - 1][0] <= self.start_alpha
            )
            if pass_through_start and i2 == -1:
                i2 = i
            elif pass_through_start and i1 == -1:
                i1 = i
                break
        if self.verbose:
            print("Track generation: %i..%i -> %i-tiles track" % (i1, i2, i2 - i1))
        assert i1 != -1
        assert i2 != -1

        track = track[i1 : i2 - 1]

        # first_beta = track[0][1]
        # first_perp_x = math.cos(first_beta)
        # first_perp_y = math.sin(first_beta)
        # # Length of perpendicular jump to put together head and tail
        # well_glued_together = np.sqrt(
        #     np.square(first_perp_x * (track[0][2] - track[-1][2]))
        #     + np.square(first_perp_y * (track[0][3] - track[-1][3]))
        # )
        # if well_glued_together > TRACK_DETAIL_STEP:
        #     return False

        # Red-white border on hard turns
        border = [False] * len(track)
        if show_borders:
            for i in range(len(track)):
                good = True
                oneside = 0
                for neg in range(BORDER_MIN_COUNT):
                    beta1 = track[i - neg - 0][1]
                    beta2 = track[i - neg - 1][1]
                    good &= abs(beta1 - beta2) > TRACK_TURN_RATE * 0.2
                    oneside += np.sign(beta1 - beta2)
                good &= abs(oneside) == BORDER_MIN_COUNT
                border[i] = good
            for i in range(len(track)):
                for neg in range(BORDER_MIN_COUNT):
                    border[i - neg] |= border[i]

        # Create tiles
        for i in range(len(track)):
            alpha1, beta1, x1, y1 = track[i]
            alpha2, beta2, x2, y2 = track[i - 1]
            road1_l = (
                x1 - TRACK_WIDTH * math.cos(beta1),
                y1 - TRACK_WIDTH * math.sin(beta1),
            )
            road1_r = (
                x1 + TRACK_WIDTH * math.cos(beta1),
                y1 + TRACK_WIDTH * math.sin(beta1),
            )
            road2_l = (
                x2 - TRACK_WIDTH * math.cos(beta2),
                y2 - TRACK_WIDTH * math.sin(beta2),
            )
            road2_r = (
                x2 + TRACK_WIDTH * math.cos(beta2),
                y2 + TRACK_WIDTH * math.sin(beta2),
            )
            vertices = [road1_l, road1_r, road2_r, road2_l]
            self.fd_tile.shape.vertices = vertices
            t = self.world.CreateStaticBody(fixtures=self.fd_tile)
            t.userData = t
            c = 0.01 * (i % 3) * 255
            t.color = self.road_color + c
            t.road_visited = False
            t.road_friction = 1.0
            t.idx = i
            t.fixtures[0].sensor = True
            self.road_poly.append(([road1_l, road1_r, road2_r, road2_l], t.color))
            self.road.append(t)
            if border[i]:
                side = np.sign(beta2 - beta1)
                b1_l = (
                    x1 + side * TRACK_WIDTH * math.cos(beta1),
                    y1 + side * TRACK_WIDTH * math.sin(beta1),
                )
                b1_r = (
                    x1 + side * (TRACK_WIDTH + BORDER) * math.cos(beta1),
                    y1 + side * (TRACK_WIDTH + BORDER) * math.sin(beta1),
                )
                b2_l = (
                    x2 + side * TRACK_WIDTH * math.cos(beta2),
                    y2 + side * TRACK_WIDTH * math.sin(beta2),
                )
                b2_r = (
                    x2 + side * (TRACK_WIDTH + BORDER) * math.cos(beta2),
                    y2 + side * (TRACK_WIDTH + BORDER) * math.sin(beta2),
                )
                self.road_poly.append(
                    (
                        [b1_l, b1_r, b2_r, b2_l],
                        (255, 255, 255) if i % 2 == 0 else (255, 0, 0),
                    )
                )
        self.track = track
        return True

    def reset_sparse_state(self):
        if self.sparse_rewards:
            self.accumulated_rewards = 0.0
            self.set_goal()

    def reset(
        self,
        *,
        seed: Optional[int] = None,
        return_info: bool = False,
        options: Optional[dict] = None,
    ):
        super().reset(seed=seed)
        if self.fixed_environment:
            self.seed(self.level_seed)

        print("In bezier reset")

        self._destroy()
        self.reward = 0.0
        self.prev_reward = 0.0
        self.tile_visited_count = 0
        self.t = 0.0
        self.new_lap = False
        self.road_poly = []
        self.track_data = None

        if self.domain_randomize:
            randomize = True
            if isinstance(options, dict):
                if "randomize" in options:
                    randomize = options["randomize"]

            self._reinit_colors(randomize)

        self.steps = 0
        self._create_track()
        beta0, x0, y0 = self.track[0][1:4]
        x0 -= self.x_offset
        y0 -= self.y_offset
        self.car = Car(self.world, beta0, x0, y0)

        self.goal_bin = None
        self.reset_sparse_state()

        self.renderer.reset()
        if not return_info:
            return self.step(None)[0]
        else:
            return self.step(None)[0], {}

    def step(self, action: Union[np.ndarray, int]):
        assert self.car is not None
        if action is not None:
            if self.continuous:
                self.car.steer(-action[0])
                self.car.gas(action[1])
                self.car.brake(action[2])
            else:
                if not self.action_space.contains(action):
                    raise InvalidAction(
                        f"you passed the invalid action `{action}`. "
                        f"The supported action_space is `{self.action_space}`"
                    )
                self.car.steer(-0.6 * (action == 1) + 0.6 * (action == 2))
                self.car.gas(0.2 * (action == 3))
                self.car.brake(0.8 * (action == 4))

        self.car.step(1.0 / FPS)
        self.world.Step(1.0 / FPS, 6 * 30, 2 * 30)
        self.t += 1.0 / FPS
        self.steps += 1

        self.state = self._render("single_state_pixels")

        step_reward = 0
        # terminated = False
        # truncated = False
        done = False
        if action is not None:  # First step without action, called from reset()
            self.reward -= 0.1
            # We actually don't want to count fuel spent, we want car to be faster.
            # self.reward -=  10 * self.car.fuel_spent / ENGINE_POWER
            self.car.fuel_spent = 0.0
            step_reward = self.reward - self.prev_reward
            self.prev_reward = self.reward
            if self.tile_visited_count == len(self.track) or self.new_lap:
                # Truncation due to finishing lap
                # This should not be treated as a failure
                # but like a timeout
                # truncated = True
                done = True
            x, y = self.car.hull.position
            if abs(x) > self.playfield or abs(y) > self.playfield:
                # terminated = True
                done = True
                step_reward = -100

        if self.sparse_rewards:
            self.accumulated_rewards += step_reward
            revealed_reward = 0
            if self.goal_reached:
                revealed_reward = self.accumulated_rewards
                self.accumulated_rewards = 0.0
                # truncated = True
                done = True
        else:
            revealed_reward = step_reward
        if self.clip_reward:
            revealed_reward = min(max(revealed_reward, -self.clip_reward), self.clip_reward)

        self.renderer.render_step()
        return self.state, revealed_reward, done, {} # terminated, truncated, {}

    def render(self, mode: str = "human"):
        if self.render_mode is not None:
            return self.renderer.get_renders()
        else:
            return self._render(mode)

    def _render(self, mode: str = "human"):
        assert mode in self.metadata["render_modes"]

        pygame.font.init()
        if self.screen is None and mode == "human":
            pygame.init()
            pygame.display.init()
            self.screen = pygame.display.set_mode((WINDOW_W, WINDOW_H))
        if self.clock is None:
            self.clock = pygame.time.Clock()

        if "t" not in self.__dict__:
            return  # reset() not called yet

        self.surf = pygame.Surface((WINDOW_W, WINDOW_H))

        assert self.car is not None
        # computing transformations
        angle = -self.car.hull.angle
        # Animating first second zoom.
        if self.birdseye or mode in ['level', 'sketch']:
            angle = 0
            zoom_coef = self.full_zoom
        else:
            zoom_coef = ZOOM
        if self.animate_zoom:
            zoom = 0.1 * SCALE * max(1 - self.t, 0) + zoom_coef * SCALE * min(self.t, 1)
        else:
            zoom = zoom_coef * SCALE
        scroll_x = -(self.car.hull.position[0]) * zoom
        scroll_y = -(self.car.hull.position[1]) * zoom
        if self.birdseye or mode in ['level', 'sketch']:
            trans = (WINDOW_W / 2, WINDOW_H / 2)
        else:
            trans = pygame.math.Vector2((scroll_x, scroll_y)).rotate_rad(angle)
            trans = (WINDOW_W / 2 + trans[0], WINDOW_H / 4 + trans[1])

        self._render_road(zoom, trans, angle)
        self.car.draw(
            self.surf,
            zoom,
            trans,
            angle,
            mode not in ["state_pixels", "single_state_pixels"],
        )

        self.surf = pygame.transform.flip(self.surf, False, True)

        # showing stats
        if mode not in ['level', 'sketch'] and self.show_indicators:
            self._render_indicators(WINDOW_W, WINDOW_H)

            font = pygame.font.Font(pygame.font.get_default_font(), 42)
            text = font.render("%04i" % self.reward, True, (255, 255, 255), (0, 0, 0))
            text_rect = text.get_rect()
            text_rect.center = (60, WINDOW_H - WINDOW_H * 2.5 / 40.0)
            self.surf.blit(text, text_rect)

        if mode == "human":
            pygame.event.pump()
            self.clock.tick(self.metadata["render_fps"])
            assert self.screen is not None
            self.screen.fill(0)
            self.screen.blit(self.surf, (0, 0))
            pygame.display.flip()

        if mode in {"rgb_array", "single_rgb_array"}:
            return self._create_image_array(self.surf, (VIDEO_W, VIDEO_H))
        elif mode in {"state_pixels", "single_state_pixels", "sketch"}:
            return self._create_image_array(self.surf, (STATE_W, STATE_H))
        elif mode == "level":
            return self._create_image_array(self.surf, (WINDOW_W, WINDOW_H))
        else:
            return self.isopen

    def _render_road(self, zoom, translation, angle):
        bounds = self.playfield #PLAYFIELD
        field = [
            (bounds, bounds),
            (bounds, -bounds),
            (-bounds, -bounds),
            (-bounds, bounds),
        ]

        # draw background
        self._draw_colored_polygon(
            self.surf, field, self.bg_color, zoom, translation, angle, clip=False
        )

        # draw grass patches
        grass_dim_bound = self.playfield / 20.0
        grass = []
        for x in range(-20, 20, 2):
            for y in range(-20, 20, 2):
                grass.append(
                    [
                        (grass_dim_bound * x + grass_dim_bound, grass_dim_bound * y + 0),
                        (grass_dim_bound * x + 0, grass_dim_bound * y + 0),
                        (grass_dim_bound * x + 0, grass_dim_bound * y + grass_dim_bound),
                        (grass_dim_bound * x + grass_dim_bound, grass_dim_bound * y + grass_dim_bound),
                    ]
                )
        for poly in grass:
            self._draw_colored_polygon(
                self.surf, poly, self.grass_color, zoom, translation, angle
            )

        # draw road
        for poly, color in self.road_poly:
            # converting to pixel coordinates
            poly = [(p[0], p[1]) for p in poly]
            color = [int(c) for c in color]
            self._draw_colored_polygon(self.surf, poly, color, zoom, translation, angle)

    def _render_indicators(self, W, H):
        s = W / 40.0
        h = H / 40.0
        color = (0, 0, 0)
        polygon = [(W, H), (W, H - 5 * h), (0, H - 5 * h), (0, H)]
        pygame.draw.polygon(self.surf, color=color, points=polygon)

        def vertical_ind(place, val):
            return [
                (place * s, H - (h + h * val)),
                ((place + 1) * s, H - (h + h * val)),
                ((place + 1) * s, H - h),
                ((place + 0) * s, H - h),
            ]

        def horiz_ind(place, val):
            return [
                ((place + 0) * s, H - 4 * h),
                ((place + val) * s, H - 4 * h),
                ((place + val) * s, H - 2 * h),
                ((place + 0) * s, H - 2 * h),
            ]

        assert self.car is not None
        true_speed = np.sqrt(
            np.square(self.car.hull.linearVelocity[0])
            + np.square(self.car.hull.linearVelocity[1])
        )

        # simple wrapper to render if the indicator value is above a threshold
        def render_if_min(value, points, color):
            if abs(value) > 1e-4:
                pygame.draw.polygon(self.surf, points=points, color=color)

        render_if_min(true_speed, vertical_ind(5, 0.02 * true_speed), (255, 255, 255))
        # ABS sensors
        render_if_min(
            self.car.wheels[0].omega,
            vertical_ind(7, 0.01 * self.car.wheels[0].omega),
            (0, 0, 255),
        )
        render_if_min(
            self.car.wheels[1].omega,
            vertical_ind(8, 0.01 * self.car.wheels[1].omega),
            (0, 0, 255),
        )
        render_if_min(
            self.car.wheels[2].omega,
            vertical_ind(9, 0.01 * self.car.wheels[2].omega),
            (51, 0, 255),
        )
        render_if_min(
            self.car.wheels[3].omega,
            vertical_ind(10, 0.01 * self.car.wheels[3].omega),
            (51, 0, 255),
        )

        render_if_min(
            self.car.wheels[0].joint.angle,
            horiz_ind(20, -10.0 * self.car.wheels[0].joint.angle),
            (0, 255, 0),
        )
        render_if_min(
            self.car.hull.angularVelocity,
            horiz_ind(30, -0.8 * self.car.hull.angularVelocity),
            (255, 0, 0),
        )

    def _draw_colored_polygon(
        self, surface, poly, color, zoom, translation, angle, clip=True
    ):
        poly = [pygame.math.Vector2(c).rotate_rad(angle) for c in poly]
        poly = [
            (c[0] * zoom + translation[0], c[1] * zoom + translation[1]) for c in poly
        ]
        # This checks if the polygon is out of bounds of the screen, and we skip drawing if so.
        # Instead of calculating exactly if the polygon and screen overlap,
        # we simply check if the polygon is in a larger bounding box whose dimension
        # is greater than the screen by MAX_SHAPE_DIM, which is the maximum
        # diagonal length of an environment object
        if self.birdseye:
            zoom_coef = self.full_zoom
        else:
            zoom_coef = ZOOM
        MAX_SHAPE_DIM = (
            max(self.playfield/20.0, TRACK_WIDTH, TRACK_DETAIL_STEP) * math.sqrt(2) * zoom_coef * SCALE
        )
        if not clip or any(
            (-MAX_SHAPE_DIM <= coord[0] <= WINDOW_W + MAX_SHAPE_DIM)
            and (-MAX_SHAPE_DIM <= coord[1] <= WINDOW_H + MAX_SHAPE_DIM)
            for coord in poly
        ):
            gfxdraw.aapolygon(self.surf, poly, color)
            gfxdraw.filled_polygon(self.surf, poly, color)

    def _create_image_array(self, screen, size):
        scaled_screen = pygame.transform.smoothscale(screen, size)
        return np.transpose(
            np.array(pygame.surfarray.pixels3d(scaled_screen)), axes=(1, 0, 2)
        )

    def close(self):
        if self.screen is not None:
            pygame.display.quit()
            self.isopen = False
            pygame.quit()


if __name__ == "__main__":
    a = np.array([0.0, 0.0, 0.0])

    def register_input():
        for event in pygame.event.get():
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_LEFT:
                    a[0] = -1.0
                if event.key == pygame.K_RIGHT:
                    a[0] = +1.0
                if event.key == pygame.K_UP:
                    a[1] = +1.0
                if event.key == pygame.K_DOWN:
                    a[2] = +0.8  # set 1.0 for wheels to block to zero rotation
                if event.key == pygame.K_RETURN:
                    global restart
                    restart = True

            if event.type == pygame.KEYUP:
                if event.key == pygame.K_LEFT:
                    a[0] = 0
                if event.key == pygame.K_RIGHT:
                    a[0] = 0
                if event.key == pygame.K_UP:
                    a[1] = 0
                if event.key == pygame.K_DOWN:
                    a[2] = 0

    env = CarRacingBezier()
    env.render()

    isopen = True
    while isopen:
        env.reset()
        total_reward = 0.0
        steps = 0
        restart = False
        while True:
            register_input()
            s, r, terminated, truncated, info = env.step(a)
            total_reward += r
            if steps % 200 == 0 or terminated or truncated:
                print("\naction " + str([f"{x:+0.2f}" for x in a]))
                print(f"step {steps} total_reward {total_reward:+0.2f}")
            steps += 1
            isopen = env.render()
            if terminated or truncated or restart or isopen is False:
                break
    env.close()
