# -*- coding: utf-8 -*-
"""
SPACE GAME — космический шутер (одиночный файл, pygame).

Десктоп:            python main.py
Смоук-тест без окна: python main.py --smoke
Браузер (GitHub Pages): собрать через pygbag, см. .github/workflows/pygbag.yml

Управление:
  мышь  — прицел (нос доворачивается на курсор)
  W / S — тяга больше / меньше
  ЛКМ   — огонь
  1..4  — рельсотрон / лазеры / атомные ракеты / плазмопушка
  R     — удерживать 3 с: самоликвидация (взрыв тем сильнее, чем больше энергии)
  ESC   — выход
"""
import math, random, sys, os, asyncio

HEADLESS = ("--smoke" in sys.argv) or (os.environ.get("SG_HEADLESS") == "1")
if not HEADLESS:
    import pygame

# ---------------------------------------------------------------- вектор
class V:
    __slots__ = ("x", "y")
    def __init__(self, x=0.0, y=0.0):
        self.x = float(x); self.y = float(y)
    def __add__(self, o):  return V(self.x + o.x, self.y + o.y)
    def __sub__(self, o):  return V(self.x - o.x, self.y - o.y)
    def __mul__(self, k):
        return V(self.x * k, self.y * k) if not isinstance(k, V) else V(self.x * k.x, self.y * k.y)
    __rmul__ = __mul__
    def __truediv__(self, k): return V(self.x / k, self.y / k)
    def length(self):      return math.hypot(self.x, self.y)
    def normalized(self):
        l = self.length()
        return V(self.x / l, self.y / l) if l > 1e-9 else V(0.0, 0.0)
    def dot(self, o):      return self.x * o.x + self.y * o.y
    def rotated(self, a):
        c, s = math.cos(a), math.sin(a)
        return V(self.x * c - self.y * s, self.x * s + self.y * c)
    def dist(self, o):     return (self - o).length()
    def copy(self):        return V(self.x, self.y)
    def __repr__(self):    return "V(%.0f,%.0f)" % (self.x, self.y)

def ang_norm(a): return (a + math.pi) % (2 * math.pi) - math.pi
def clamp(v, a, b): return max(a, min(b, v))

# ---------------------------------------------------------------- константы
TICK = 60
WORLD = 6000.0            # арена 6000x6000, координаты 0..6000
WALL = 120.0              # мягкая стена
CAP_MAX = 300.0           # конденсатор
OVERLOAD = 500.0          # предел накопления
OVERHEAT_E = OVERLOAD * 0.9

SHIP = {
    "cruiser":    dict(hp=5000, thrust=120, vmax=220, turn=2.5, cost=10.0, r=30),
    "fighter":    dict(hp=1750,  thrust=200, vmax=320, turn=4.375, cost=8.0,  r=16),
    "raider":     dict(hp=3000,  thrust=260, vmax=380, turn=3.75, cost=10.0, r=20),
    "battleship": dict(hp=17500, thrust=40,  vmax=90,  turn=1, cost=15.0, r=55),
}
REACTOR_OUT = {"cruiser": 100.0, "fighter": 100.0, "raider": 100.0, "battleship": 150.0}

RAIL_DMG, RAIL_SPEED, RAIL_CD = 35.0, 900.0, 1.0 / 3.0
LASER_DMG, LASER_DT, LASER_DRAIN, LASER_RANGE = 18.0, 0.2, 25.0, 1200.0
LASER_CONE, LASER_LOCK_T = math.radians(5), 0.5
ROCKET_DMG, ROCKET_SPEED, ROCKET_TURN, ROCKET_CD = 400.0, 320.0, 2.0, 4.0
ROCKET_SPLASH, ROCKET_EDGE, ROCKET_HP = 150.0, 100.0, 30.0
PLASMA_DMG, PLASMA_SPEED, PLASMA_CD, PLASMA_COST, PLASMA_R = 350.0, 350.0, 3.0, 120.0, 12.0
SD_HOLD, SD_FLEE_R = 3.0, 700.0
RAM_MIN, RAM_K = 50.0, 0.5
LOOT_CHANCE, LOOT_LIFE, LOOT_PICK = 0.6, 15.0, 45.0

WAVES = [
    [("fighter", 8)],
    [("fighter", 12)],
    [("fighter", 16), ("raider", 2)],
    [("fighter", 16), ("raider", 4)],
    [("fighter", 12), ("raider", 4)],
    [("fighter", 12), ("raider", 6)],
    [("fighter", 20), ("raider", 6)],
    [("raider", 8), ("battleship", 2)],
]

WEAPON_NAMES = {"railgun": "РЕЛЬСОТРОН", "laser": "ЛАЗЕРЫ",
                "rocket": "АТОМНЫЕ РАКЕТЫ", "plasma": "ПЛАЗМОПУШКА"}

# ---------------------------------------------------------------- сущности
class Bullet:
    def __init__(self, pos, vel, dmg, owner):
        self.pos = pos.copy(); self.vel = vel.copy()
        self.dmg = dmg; self.owner = owner; self.life = 3.0; self.dead = False

class PlasmaBolt:
    def __init__(self, pos, vel, owner):
        self.pos = pos.copy(); self.vel = vel.copy()
        self.owner = owner; self.life = 8.0; self.dead = False

class Rocket:
    def __init__(self, pos, angle, target, owner):
        self.pos = pos.copy()
        self.vel = V(math.cos(angle), math.sin(angle)) * 120.0
        self.angle = angle; self.target = target; self.owner = owner
        self.hp = ROCKET_HP; self.life = 12.0; self.dead = False

class Explosion:
    def __init__(self, pos, radius, dmg):
        self.pos = pos.copy(); self.radius = radius
        self.dmg = dmg; self.life = 0.6; self.t = 0.0

class Beam:
    def __init__(self, a, b):
        self.a = a.copy(); self.b = b.copy(); self.life = 0.08

class Loot:
    def __init__(self, pos, kind):
        self.pos = pos.copy(); self.kind = kind; self.life = LOOT_LIFE

class Asteroid:
    def __init__(self, pos, r):
        self.pos = pos.copy(); self.r = r; self.hp = 200.0

# ---------------------------------------------------------------- корабль
class Ship:
    def __init__(self, kind, pos, is_player=False):
        st = SHIP[kind]
        self.kind = kind; self.is_player = is_player
        self.pos = pos.copy(); self.vel = V()
        self.angle = random.uniform(0, 6.28); self.aim = self.angle
        self.maxhp = st["hp"]; self.hp = st["hp"]
        self.r = st["r"]; self.thrust = st["thrust"]; self.vmax = st["vmax"]
        self.turn = st["turn"]; self.cost = st["cost"]; self.out = REACTOR_OUT[kind]
        self.energy = CAP_MAX
        self.throttle = 0.0
        self.ammo = {"railgun": 200, "rocket": 8}
        self.cd = {"railgun": 0.0, "rocket": 0.0, "plasma": 0.0}
        self.laser_left = 0.0; self.laser_pulse = 0.0
        self.lock = 0.0
        self.beam = 0.0; self.beam_a = None; self.beam_b = None
        self.alive = True; self.engine_lock = 0.0
        self.sd = 0.0; self.sd_holding = False
        self.flee = 0.0
        self.rock_cd = 0.0; self.flash = 0.0
        self.wants = {"railgun": False, "laser": False, "rocket": False, "plasma": False}
        self.weapon = "railgun"
        # ИИ
        self.auto = False
        self.ai_state = "approach"; self.ai_t = 0.0
        self.orbit_dir = random.choice((-1, 1)); self.dash_cd = 0.0
        self.juke_cd = 0.0

    # --- прицел ------------------------------------------------------
    def rotate_toward(self, target, dt):
        d = ang_norm(target - self.angle)
        rate = self.turn * dt
        self.angle += clamp(d, -rate, rate)
        return abs(d) < 0.15

    # --- оружие ------------------------------------------------------
    def process_weapons(self, dt, game):
        for k in self.cd:
            self.cd[k] = max(0.0, self.cd[k] - dt)
        self.beam = max(0.0, self.beam - dt)
        self.flash = max(0.0, self.flash - dt)

        # захват цели для лазера
        tgt = self.aim_target
        if tgt is not None and tgt.alive:
            d = self.pos.dist(tgt.pos)
            diff = abs(ang_norm(math.atan2(tgt.pos.y - self.pos.y, tgt.pos.x - self.pos.x) - self.angle))
            if d < LASER_RANGE and diff < LASER_CONE:
                self.lock += dt
            else:
                self.lock = 0.0
        else:
            self.lock = 0.0

        if self.wants.get("laser"):
            if self.laser_left <= 0.0 and self.energy > 1.0:
                self.laser_left = 3.0; self.laser_pulse = 0.0
            if self.laser_left > 0.0:
                if self.energy <= 0.5:
                    self.laser_left = 0.0
                else:
                    self.energy = max(0.0, self.energy - LASER_DRAIN * dt)
                    self.laser_left -= dt
                    self.laser_pulse -= dt
                    if self.laser_pulse <= 0.0:
                        self.laser_pulse = LASER_DT
                        self.fire_beam(game)
        else:
            self.laser_left = 0.0

        if self.wants.get("railgun") and self.cd["railgun"] <= 0 and self.ammo["railgun"] > 0:
            self.cd["railgun"] = RAIL_CD
            self.ammo["railgun"] -= 1
            d = V(math.cos(self.angle), math.sin(self.angle))
            game.bullets.append(Bullet(self.pos + d * (self.r + 12), d * RAIL_SPEED, RAIL_DMG, self))

        if self.wants.get("rocket") and self.cd["rocket"] <= 0 and self.ammo["rocket"] > 0:
            tgt = self.aim_target
            if tgt is not None and tgt.alive:
                self.cd["rocket"] = ROCKET_CD
                self.ammo["rocket"] -= 1
                game.rockets.append(Rocket(self.pos, self.angle, tgt, self))

        if self.wants.get("plasma") and self.cd["plasma"] <= 0 and self.energy >= PLASMA_COST:
            self.cd["plasma"] = PLASMA_CD
            self.energy -= PLASMA_COST
            d = V(math.cos(self.angle), math.sin(self.angle))
            game.bolts.append(PlasmaBolt(self.pos + d * (self.r + 14), d * PLASMA_SPEED, self))

    def fire_beam(self, game):
        d = V(math.cos(self.angle), math.sin(self.angle))
        best, best_t = None, 1e18
        for s in game.ships:
            if s is self or not s.alive:
                continue
            rel = s.pos - self.pos
            t = rel.dot(d)
            if t < 0 or t > LASER_RANGE:
                continue
            perp = (rel - d * t).length()
            if perp < s.r + 6 and t < best_t:
                best, best_t = s, t
        end = self.pos + d * (best_t if best else LASER_RANGE)
        game.beams.append(Beam(self.pos, end))
        if best is not None:
            dmg = LASER_DMG if self.lock >= LASER_LOCK_T else LASER_DMG * 0.5
            best.damage(dmg, game)

    # --- апдейт ------------------------------------------------------
    def update(self, dt, game):
        if not self.alive:
            return
        self.engine_lock = max(0.0, self.engine_lock - dt)
        self.rock_cd = max(0.0, self.rock_cd - dt)

        if self.is_player and not self.auto:
            pass  # aim/throttle задаёт ввод
        else:
            self.ai(dt, game)

        self.rotate_toward(self.aim, dt)

        thr = self.throttle
        if self.engine_lock > 0.0:
            thr = min(thr, 0.25)
        speed = self.vel.length()
        acc = self.thrust * max(0.0, 1.0 - speed / self.vmax) * thr
        self.vel += V(math.cos(self.angle), math.sin(self.angle)) * (acc * dt)
        self.pos += self.vel * dt

        # мягкие стены
        if self.pos.x < WALL:     self.pos.x = WALL;     self.vel.x *= -0.3
        if self.pos.x > WORLD - WALL: self.pos.x = WORLD - WALL; self.vel.x *= -0.3
        if self.pos.y < WALL:     self.pos.y = WALL;     self.vel.y *= -0.3
        if self.pos.y > WORLD - WALL: self.pos.y = WORLD - WALL; self.vel.y *= -0.3

        # энергия
        cons = self.cost * (1.0 + 2.2 * thr * thr)
        self.energy += (self.out - cons) * dt
        if self.energy >= OVERLOAD:
            self.energy = OVERLOAD
        if self.energy >= OVERHEAT_E and thr > 0.0 and self.engine_lock <= 0.0:
            self.engine_lock = 3.0
            self.energy = OVERHEAT_E
        if self.energy < 0.0:
            self.energy = 0.0

        self.process_weapons(dt, game)

        # самоликвидация
        if self.sd_holding and self.alive:
            self.sd += dt
            if self.sd > 1.5:
                for s in game.ships:
                    if s is not self and s.alive and s.pos.dist(self.pos) < SD_FLEE_R:
                        s.flee = max(s.flee, 1.2)
            if self.sd >= SD_HOLD:
                self.detonate(game)
        else:
            self.sd = 0.0

    def detonate(self, game):
        if not self.alive:
            return
        dmg = self.energy * 2.0
        radius = 300.0 + self.energy
        game.explosions.append(Explosion(self.pos, radius, dmg))
        for s in game.ships:
            if s is self or not s.alive:
                continue
            d = s.pos.dist(self.pos)
            if d < radius + s.r:
                t = clamp(d / radius, 0.0, 1.0)
                s.damage(dmg * (0.3 + 0.7 * (1.0 - t)), game)
        self.hp = 0.0
        self.alive = False
        self.death_drops(game)

    def damage(self, d, game):
        if not self.alive:
            return
        self.hp -= d
        self.flash = 0.1
        if self.hp <= 0:
            self.hp = 0.0; self.alive = False
            if not self.is_player:
                game.kills += 1
            self.death_drops(game)

    def death_drops(self, game):
        if self.is_player:
            return
        if random.random() < LOOT_CHANCE:
            kind = random.choice(("ammo", "rocket", "cell"))
            game.loots.append(Loot(self.pos, kind))

    # --- ИИ ----------------------------------------------------------
    def nearest_enemy(self, game):
        best, bd = None, 1e18
        for s in game.ships:
            if s.alive and s is not self:
                d = self.pos.dist(s.pos)
                if d < bd:
                    best, bd = s, d
        return best, bd

    def lead_aim(self, tgt, bullet_speed):
        t = self.pos.dist(tgt.pos) / bullet_speed
        p = tgt.pos + tgt.vel * (t * 0.8)
        return math.atan2(p.y - self.pos.y, p.x - self.pos.x)

    def ai(self, dt, game):
        tgt, dist = self.nearest_enemy(game)
        self.aim_target = tgt
        for k in self.wants:
            self.wants[k] = False
        self.throttle = 0.0
        if tgt is None:
            return
        if self.flee > 0.0:
            self.flee -= dt
            self.aim = math.atan2(self.pos.y - tgt.pos.y, self.pos.x - tgt.pos.x)
            self.throttle = 1.0
            return

        if self.kind == "fighter":
            if self.hp < 0.3 * self.maxhp and self.ai_state != "flee":
                self.ai_state, self.ai_t = "flee", 5.0
            if self.ai_state == "flee":
                self.ai_t -= dt
                self.aim = math.atan2(self.pos.y - tgt.pos.y, self.pos.x - tgt.pos.x)
                self.throttle = 1.0
                if self.ai_t <= 0.0:
                    self.ai_state = "approach"
            elif self.ai_state == "approach":
                self.aim = self.lead_aim(tgt, RAIL_SPEED)
                self.throttle = 1.0
                if dist < 500.0:
                    self.ai_state = "orbit"
                if dist < 900.0:
                    self.wants["railgun"] = True
            else:  # orbit
                base = math.atan2(self.pos.y - tgt.pos.y, self.pos.x - tgt.pos.x)
                self.aim = base + self.orbit_dir * (math.pi / 2 + 0.3)
                self.throttle = 0.85
                if dist > 550.0:
                    self.ai_state = "approach"
                if dist < 650.0:
                    self.wants["laser"] = True
                if dist < 900.0 and abs(ang_norm(self.aim - self.angle)) < 0.15:
                    self.wants["railgun"] = True

        elif self.kind == "raider":
            self.dash_cd -= dt
            to_t = math.atan2(tgt.pos.y - self.pos.y, tgt.pos.x - self.pos.x)
            if dist > 1200.0:
                self.aim = to_t; self.throttle = 1.0
            elif dist < 800.0:
                self.aim = to_t + math.pi * 0.85 * self.orbit_dir
                self.throttle = 1.0
            else:
                self.aim = to_t + self.orbit_dir * math.pi / 2
                self.throttle = 0.7
            if self.dash_cd <= 0.0:
                self.dash_cd = 6.0
                side = V(math.cos(self.angle + math.pi / 2), math.sin(self.angle + math.pi / 2))
                self.vel += side * 60.0 * self.orbit_dir
            if dist < 1200.0 and abs(ang_norm(self.aim - self.angle)) < 0.35:
                self.wants["rocket"] = True
            if dist < 600.0 and abs(ang_norm(self.aim - self.angle)) < 0.2:
                self.wants["laser"] = True

        else:  # battleship
            if dist > 2500.0:
                self.aim = self.lead_aim(tgt, PLASMA_SPEED)
                self.throttle = 0.6
            else:
                self.aim = self.lead_aim(tgt, PLASMA_SPEED)
                self.throttle = 0.1
            if dist < 2200.0 and self.energy >= PLASMA_COST and abs(ang_norm(self.aim - self.angle)) < 0.08:
                self.wants["plasma"] = True
            if dist < 1200.0 and abs(ang_norm(self.aim - self.angle)) < 0.12:
                self.wants["railgun"] = True

    # ИИ крейсера (для смоук-теста)
    def auto_ai(self, dt, game):
        tgt, dist = self.nearest_enemy(game)
        self.aim_target = tgt
        for k in self.wants:
            self.wants[k] = False
        if tgt is None:
            self.throttle = 0.0
            return
        self.juke_cd -= dt
        for e in list(game.bolts) + list(game.rockets):
            if e.owner is not self and e.pos.dist(self.pos) < 700.0 and self.juke_cd <= 0.0:
                self.orbit_dir *= -1
                self.juke_cd = 2.0
        to_t = math.atan2(tgt.pos.y - self.pos.y, tgt.pos.x - self.pos.x)
        diff = abs(ang_norm(to_t - self.angle))
        if tgt.kind == "battleship":
            ideal = 1200.0
            if dist > ideal + 100:
                self.aim, self.throttle = to_t, 1.0
            elif dist < ideal - 100:
                self.aim, self.throttle = to_t + math.pi * 0.9 * self.orbit_dir, 1.0
            else:
                self.aim, self.throttle = to_t + self.orbit_dir * math.pi / 2, 0.6
            if self.ammo["rocket"] > 0 and diff < 0.35:
                self.wants["rocket"] = True
            elif diff < 0.12:
                self.wants["railgun"] = True
        elif tgt.kind == "raider":
            ideal = 1100.0
            if dist > ideal + 100:
                self.aim, self.throttle = to_t, 1.0
            elif dist < ideal - 150:
                self.aim, self.throttle = to_t + math.pi * 0.9 * self.orbit_dir, 1.0
            else:
                self.aim, self.throttle = to_t + self.orbit_dir * math.pi / 2, 0.7
            if diff < 0.15:
                self.wants["railgun"] = True
            if dist < 650.0:
                self.wants["laser"] = True
        else:
            if dist > 650.0:
                self.aim, self.throttle = to_t, 1.0
            else:
                self.aim, self.throttle = to_t + self.orbit_dir * math.pi / 2, 0.8
            if diff < 0.15 and dist < 1000.0:
                self.wants["railgun"] = True
            if dist < 700.0:
                self.wants["laser"] = True
            if self.ammo["rocket"] > 0 and diff < 0.3 and dist < 900.0:
                self.wants["rocket"] = True

# ---------------------------------------------------------------- игра
class Game:
    def __init__(self):
        self.running = True
        self.over = False
        self.kills = 0
        self.wave = 0
        self.wave_clear = 0.0
        self.in_wave = False
        self.ships = []
        self.bullets = []
        self.bolts = []
        self.rockets = []
        self.explosions = []
        self.beams = []
        self.loots = []
        self.asteroids = []
        self.cam = V(WORLD / 2, WORLD / 2)
        self.screen = None
        self.font = None
        self.stars = [(random.uniform(0, WORLD), random.uniform(0, WORLD),
                       random.uniform(0.4, 1.0)) for _ in range(400)]
        self.reset()

    def reset(self):
        self.ships = []; self.bullets = []; self.bolts = []; self.rockets = []
        self.explosions = []; self.beams = []; self.loots = []
        self.asteroids = [Asteroid(V(random.uniform(800, WORLD - 800),
                                    random.uniform(800, WORLD - 800)),
                                   random.uniform(40, 90)) for _ in range(10)]
        self.kills = 0; self.over = False; self.wave = 0
        self.in_wave = False; self.wave_clear = 0.0
        self.player = Ship("cruiser", V(WORLD / 2, WORLD / 2), is_player=True)
        self.player.auto = getattr(self, "_player_auto", True)  # в GUI станет False
        self.ships.append(self.player)
        self.cam = self.player.pos.copy()
        self.start_wave()

    def start_wave(self):
        self.wave += 1
        if self.wave <= len(WAVES):
            comp = WAVES[self.wave - 1]
            scale = 1.0
        else:
            comp = WAVES[-1]
            extra = (self.wave - len(WAVES)) // 2
            comp = [(k, n + extra) for k, n in comp]
            scale = 1.1 ** (self.wave - len(WAVES))
        for kind, n in comp:
            for _ in range(n):
                self.spawn_bot(kind, scale)
        self.in_wave = True

    def spawn_bot(self, kind, scale=1.0):
        for _ in range(50):
            side = random.randint(0, 3)
            p = random.uniform(WALL + 200, WORLD - WALL - 200)
            if side == 0:   pos = V(p, WALL + 100)
            elif side == 1: pos = V(p, WORLD - WALL - 100)
            elif side == 2: pos = V(WALL + 100, p)
            else:           pos = V(WORLD - WALL - 100, p)
            if pos.dist(self.player.pos) > 1500.0:
                break
        else:
            pos = V(WALL + 100, WALL + 100)
        s = Ship(kind, pos)
        s.maxhp *= scale; s.hp = s.maxhp
        self.ships.append(s)

    # --- ввод --------------------------------------------------------
    def read_input(self):
        inp = {"up": False, "down": False, "fire": False,
               "sd": False, "weapon": None, "aim": None, "enter": False, "esc": False}
        if HEADLESS or pygame is None:
            return inp
        keys = pygame.key.get_pressed()
        mx, my = pygame.mouse.get_pos()
        inp["up"] = keys[pygame.K_w]
        inp["down"] = keys[pygame.K_s]
        inp["fire"] = pygame.mouse.get_pressed()[0]
        inp["sd"] = keys[pygame.K_r]
        for key, w in ((pygame.K_1, "railgun"), (pygame.K_2, "laser"),
                       (pygame.K_3, "rocket"), (pygame.K_4, "plasma")):
            if keys[key]:
                inp["weapon"] = w
        inp["aim"] = self.screen_to_world(V(mx, my))
        inp["enter"] = keys[pygame.K_RETURN]
        inp["esc"] = keys[pygame.K_ESCAPE]
        return inp

    def screen_to_world(self, p):
        return self.cam + (p - V(1280 / 2, 720 / 2))

    def apply_input(self, inp):
        p = self.player
        if not p.alive:
            if inp["enter"]:
                self.reset()
            return
        if inp["esc"]:
            self.running = False
            return
        if inp["weapon"]:
            p.weapon = inp["weapon"]
        if inp["up"]:   p.throttle = clamp(p.throttle + 0.02, 0.0, 1.0)
        if inp["down"]: p.throttle = clamp(p.throttle - 0.02, 0.0, 1.0)
        p.aim = math.atan2(inp["aim"].y - p.pos.y, inp["aim"].x - p.pos.x)
        p.aim_target = None
        best, bd = None, 1e18
        for s in self.ships:
            if s.alive and s is not p:
                d = s.pos.dist(p.pos)
                if d < bd:
                    best, bd = s, d
        p.aim_target = best
        p.wants = {"railgun": False, "laser": False, "rocket": False, "plasma": False}
        if inp["fire"]:
            p.wants[p.weapon] = True
        p.sd_holding = inp["sd"]

    # --- апдейт ------------------------------------------------------
    def update(self, dt):
        if self.over:
            return
        p = self.player
        if p.auto:
            p.auto_ai(dt, self)

        for s in self.ships:
            s.update(dt, self)

        # таран
        for i in range(len(self.ships)):
            for j in range(i + 1, len(self.ships)):
                a, b = self.ships[i], self.ships[j]
                if not (a.alive and b.alive):
                    continue
                d = a.pos.dist(b.pos)
                if d < a.r + b.r and d > 0.001:
                    rel = (a.vel - b.vel).length()
                    if rel > RAM_MIN:
                        a.damage(RAM_K * rel, self)
                        b.damage(RAM_K * rel, self)
                    push = (b.pos - a.pos).normalized() * (a.r + b.r - d) * 0.5
                    a.pos -= push; b.pos += push

        # астероиды: столкновения
        for s in self.ships:
            if not s.alive or s.rock_cd > 0.0:
                continue
            for a in self.asteroids:
                d = s.pos.dist(a.pos)
                if d < a.r + s.r:
                    s.damage(100.0, self)
                    s.rock_cd = 0.5
                    n = (s.pos - a.pos).normalized()
                    s.pos = a.pos + n * (a.r + s.r + 2)
                    s.vel = s.vel.reflected(n) if hasattr(s.vel, "reflected") else s.vel - n * (2 * s.vel.dot(n))
                    break

        # пули
        for b in self.bullets:
            if b.dead:
                continue
            b.life -= dt
            b.pos += b.vel * dt
            if b.life <= 0:
                b.dead = True; continue
            for a in self.asteroids:
                if b.pos.dist(a.pos) < a.r:
                    b.dead = True; break
            if b.dead:
                continue
            for s in self.ships:
                if s.alive and s is not b.owner and b.pos.dist(s.pos) < s.r + 5:
                    s.damage(b.dmg, self); b.dead = True; break
            if b.dead:
                continue
            for r in self.rockets:
                if not r.dead and r.owner is not b.owner and b.pos.dist(r.pos) < 20.0:
                    r.hp -= b.dmg
                    if r.hp <= 0:
                        r.dead = True
                        self.explosions.append(Explosion(r.pos, 100.0, 150.0))
                        self.splash(r.pos, 100.0, 150.0)
                    b.dead = True
                    break
        self.bullets = [b for b in self.bullets if not b.dead]

        # ракеты
        for r in self.rockets:
            if r.dead:
                continue
            r.life -= dt
            if r.life <= 0:
                r.dead = True
                self.splash(r.pos, ROCKET_DMG, ROCKET_SPLASH)
                self.explosions.append(Explosion(r.pos, ROCKET_SPLASH, ROCKET_DMG))
                continue
            if r.target and r.target.alive:
                want = math.atan2(r.target.pos.y - r.pos.y, r.target.pos.x - r.pos.x)
                d = ang_norm(want - r.angle)
                r.angle += clamp(d, -ROCKET_TURN * dt, ROCKET_TURN * dt)
            sp = min(ROCKET_SPEED, r.vel.length() + 200.0 * dt)
            r.vel = V(math.cos(r.angle), math.sin(r.angle)) * sp
            r.pos += r.vel * dt
            hit = None
            for s in self.ships:
                if s.alive and s is not r.owner and r.pos.dist(s.pos) < s.r + 15:
                    hit = s; break
            if hit is None:
                for a in self.asteroids:
                    if r.pos.dist(a.pos) < a.r:
                        hit = a; break
            if hit is not None:
                r.dead = True
                self.splash(r.pos, ROCKET_DMG, ROCKET_SPLASH)
                self.explosions.append(Explosion(r.pos, ROCKET_SPLASH, ROCKET_DMG))
        self.rockets = [r for r in self.rockets if not r.dead]

        # плазма
        for b in self.bolts:
            if b.dead:
                continue
            b.life -= dt
            b.pos += b.vel * dt
            if b.life <= 0:
                b.dead = True; continue
            for s in self.ships:
                if s.alive and s is not b.owner and b.pos.dist(s.pos) < s.r + PLASMA_R:
                    s.damage(PLASMA_DMG, self)
                    self.explosions.append(Explosion(b.pos, 60.0, 0.0))
                    b.dead = True
                    break
        self.bolts = [b for b in self.bolts if not b.dead]

        # взрывы (визуал) и лучи
        for e in self.explosions:
            e.t += dt; e.life -= dt
        self.explosions = [e for e in self.explosions if e.life > 0]
        for bm in self.beams:
            bm.life -= dt
        self.beams = [bm for bm in self.beams if bm.life > 0]

        # лут
        for l in self.loots:
            l.life -= dt
            if p.alive and p.pos.dist(l.pos) < LOOT_PICK:
                if l.kind == "ammo":    p.ammo["railgun"] = min(400, p.ammo["railgun"] + 40)
                elif l.kind == "rocket": p.ammo["rocket"] = min(16, p.ammo["rocket"] + 1)
                else:                   p.energy = min(OVERLOAD, p.energy + 100.0)
                l.life = 0.0
        self.loots = [l for l in self.loots if l.life > 0]

        # волны
        bots_alive = sum(1 for s in self.ships if s.alive and not s.is_player)
        self.ships = [s for s in self.ships if s.alive or s.is_player]
        if self.in_wave and bots_alive == 0:
            self.in_wave = False
            self.wave_clear = 20.0
        if not self.in_wave:
            self.wave_clear -= dt
            if self.wave_clear <= 0.0:
                self.start_wave()

        if not self.player.alive:
            self.over = True
        self.cam = self.cam + (self.player.pos - self.cam) * 0.1

    def splash(self, pos, dmg, radius):
        for s in self.ships:
            if not s.alive:
                continue
            d = s.pos.dist(pos)
            if d < radius + s.r:
                t = clamp(d / radius, 0.0, 1.0)
                s.damage(dmg * (0.3 + 0.7 * (1.0 - t)), self)

    # --- отрисовка ---------------------------------------------------
    def init_screen(self):
        pygame.init()
        self.screen = pygame.display.set_mode((1280, 720))
        pygame.display.set_caption("SPACE GAME")
        self.font = pygame.font.Font(None, 22)
        self.font_big = pygame.font.Font(None, 44)
        self._player_auto = False
        self.player.auto = False

    def draw(self):
        if HEADLESS:
            return
        scr = self.screen
        scr.fill((8, 10, 18))
        off = V(1280 / 2, 720 / 2) - self.cam
        def w2s(p): return (int(p.x + off.x), int(p.y + off.y))
        for x, y, br in self.stars:
            c = int(60 + 140 * br)
            scr.set_at((int((x + off.x) % 1280), int((y + off.y) % 720)), (c, c, c))
        pygame.draw.rect(scr, (60, 70, 110),
                         pygame.Rect(int(off.x), int(off.y), WORLD, WORLD), 2)
        for a in self.asteroids:
            pygame.draw.circle(scr, (90, 80, 70), w2s(a.pos), int(a.r))
        for l in self.loots:
            col = {"ammo": (200, 200, 120), "rocket": (255, 140, 90), "cell": (120, 220, 255)}[l.kind]
            pygame.draw.rect(scr, col, pygame.Rect(w2s(l.pos)[0] - 6, w2s(l.pos)[1] - 6, 12, 12))
        for b in self.bullets:
            p1 = w2s(b.pos); p2 = w2s(b.pos - b.vel.normalized() * 18)
            pygame.draw.line(scr, (255, 230, 140), p1, p2, 2)
        for bm in self.beams:
            pygame.draw.line(scr, (120, 255, 180), w2s(bm.a), w2s(bm.b), 2)
        for b in self.bolts:
            pygame.draw.circle(scr, (180, 120, 255), w2s(b.pos), int(PLASMA_R))
        for r in self.rockets:
            pygame.draw.circle(scr, (255, 110, 60), w2s(r.pos), 5)
        for e in self.explosions:
            k = 1.0 - e.life / 0.6
            rad = int(e.radius * (0.2 + 0.8 * k))
            if rad > 2:
                pygame.draw.circle(scr, (255, int(200 - 120 * k), 60), w2s(e.pos), rad, 2)
        for s in self.ships:
            if not s.alive:
                continue
            col = (100, 180, 255) if s.is_player else (255, 90, 90)
            if s.flash > 0:
                col = (255, 255, 255)
            pts = []
            for dx, dy in ((1.4, 0), (-0.9, 0.7), (-0.5, 0), (-0.9, -0.7)):
                pts.append(w2s(s.pos + V(dx, dy).rotated(s.angle) * s.r))
            pygame.draw.polygon(scr, col, pts)
            if s.sd > 0.0:
                k = (s.sd / SD_HOLD) % 0.4
                pygame.draw.circle(scr, (255, 120, 120), w2s(s.pos), int(40 + 120 * k), 2)
        self.draw_hud()
        pygame.display.flip()

    def draw_hud(self):
        p = self.player
        scr, f = self.screen, self.font
        def bar(x, y, w, h, frac, col, label):
            pygame.draw.rect(scr, (40, 44, 60), pygame.Rect(x, y, w, h))
            pygame.draw.rect(scr, col, pygame.Rect(x, y, int(w * clamp(frac, 0, 1)), h))
            scr.blit(f.render(label, True, (230, 230, 230)), (x + 6, y + 2))
        bar(20, 16, 300, 20, p.hp / p.maxhp, (80, 200, 120), "HP %.0f" % p.hp)
        bar(20, 42, 300, 20, p.energy / OVERLOAD, (90, 180, 255),
            "ЭНЕРГИЯ %.0f / %.0f" % (p.energy, OVERLOAD))
        if p.engine_lock > 0:
            scr.blit(f.render("ПЕРЕГРЕВ ДВИГАТЕЛЕЙ %.1f с" % p.engine_lock, True, (255, 120, 90)), (20, 68))
        wname = WEAPON_NAMES[p.weapon]
        ammo = ""
        if p.weapon == "railgun": ammo = "  %d снарядов" % p.ammo["railgun"]
        if p.weapon == "rocket":  ammo = "  %d ракет" % p.ammo["rocket"]
        if p.weapon == "laser":   ammo = "  захват %.1f с" % p.lock
        scr.blit(f.render("[1..4] " + wname + ammo, True, (255, 230, 140)), (20, 92))
        scr.blit(f.render("Волна %d   Убито: %d   Врагов: %d" %
                          (self.wave, self.kills,
                           sum(1 for s in self.ships if s.alive and not s.is_player)), True, (200, 200, 220)), (20, 114))
        if p.sd > 0.0:
            k = p.sd / SD_HOLD
            pygame.draw.rect(scr, (60, 30, 30), pygame.Rect(540, 16, 200, 24))
            pygame.draw.rect(scr, (255, 80, 60), pygame.Rect(540, 16, int(200 * k), 24))
            scr.blit(f.render("САМОЛИКВИДАЦИЯ %.1f с (R — отпустить)" % (SD_HOLD - p.sd), True, (255, 200, 180)), (548, 20))
        if not p.alive or self.over:
            t = self.font_big.render("КОРАБЛЬ УНИЧТОЖЕН — Enter: заново", True, (255, 120, 90))
            scr.blit(t, (1280 // 2 - t.get_width() // 2, 720 // 2 - 40))

    def handle_events(self):
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                self.running = False

# ---------------------------------------------------------------- главный цикл
async def main():
    game = Game()
    game.init_screen()
    clock = pygame.time.Clock()
    while game.running:
        dt = clock.tick(TICK) / 1000.0
        game.handle_events()
        inp = game.read_input()
        game.apply_input(inp)
        game.update(dt)
        game.draw()
        await asyncio.sleep(0)

# ---------------------------------------------------------------- смоук-тест
def smoke():
    random.seed(7)
    print("=== ТЕСТ ВЗРЫВА (энергия 500 EU) ===")
    g = Game()
    g.asteroids = []
    p = g.player
    p.pos = V(1000, 1000); p.energy = 500.0
    f = Ship("fighter", V(1000, 1000))
    r = Ship("raider", V(1600, 1000))
    bs = Ship("battleship", V(1800, 1000))
    g.ships += [f, r, bs]
    for s in (f, r, bs):
        s.ai = lambda dt, g: None
        s.aim_target = None
    p.sd_holding = True
    for _ in range(int(SD_HOLD * TICK) + 2):
        g.update(1.0 / TICK)
    print("истребитель (350 HP): жив=%s, HP=%.0f" % (f.alive, f.hp))
    print("рейдер (600 HP): жив=%s, HP=%.0f" % (r.alive, r.hp))
    print("линкор (3500 HP): жив=%s, HP=%.0f" % (bs.alive, bs.hp))

    print("=== ДУЭЛИ В-ЛОБ (цели неподвижны, чистый обмен DPS) ===")
    def static_duel(a_weapon, b_kind, max_t=120.0):
        g = Game(); g.asteroids = []
        a = g.player; a.auto = False
        a.pos = V(2500, 3000); a.vel = V(); a.angle = 0.0; a.aim = 0.0
        b = Ship(b_kind, V(3300, 3000)); b.angle = math.pi; b.aim = math.pi
        g.ships.append(b)
        def bai(dt, gg):
            for k in b.wants:
                b.wants[k] = False
            if b.kind == "fighter":
                b.wants["railgun"] = True
            elif b.kind == "raider":
                b.wants["rocket"] = True
            else:
                b.wants["railgun"] = True; b.wants["plasma"] = True
            b.aim_target = a; b.throttle = 0.0
        b.ai = bai
        a.aim_target = b
        t = 0.0
        while a.alive and b.alive and t < max_t:
            a.wants = {"railgun": a_weapon == "railgun", "laser": a_weapon == "laser",
                       "rocket": a_weapon == "rocket", "plasma": a_weapon == "plasma"}
            g.update(1.0 / TICK)
            t += 1.0 / TICK
        return t, b.alive, a.alive
    t, bw, aw = static_duel("railgun", "fighter")
    print("крейсер(рельсотрон) vs истребитель: истребитель %s за %.1f с; игрок %s" %
          ("убит" if not bw else "выстоял", t, "жив" if aw else "погиб"))
    t, bw, aw = static_duel("rocket", "raider")
    print("крейсер(ракеты) vs рейдер: %s за %.1f с" %
          ("рейдер убит, игрок жив" if not bw and aw else
           ("обоюдное уничтожение" if not bw and not aw else
            ("игрок жив, рейдер выстоял" if bw and aw else "игрок погиб, рейдер выстоял")), t))
    t, bw, aw = static_duel("plasma", "battleship", 120.0)
    print("крейсер(плазма) vs линкор: %s за %.1f с — в лоб линкор не берётся, нужна тактика" %
          ("линкор убит, игрок жив" if not bw and aw else
           ("обоюдное уничтожение" if not bw and not aw else
            ("линкор выстоял" if bw and not aw else "рейдер?")), t))
    print("=== ВЫЖИВАНИЕ 90 с (авто-ИИ) ===")
    g = Game(); g.asteroids = []
    g.player.auto = True
    t = 0.0
    while t < 90.0 and g.player.alive:
        g.update(1.0 / TICK)
        t += 1.0 / TICK
    print("время: %.0f с, волна %d, убито %d, HP игрока %.0f, жив" % (t, g.wave, g.kills, g.player.hp) if g.player.alive else
          "время: %.0f с, волна %d, убито %d — игрок погиб" % (t, g.wave, g.kills))
    print("=== СМОУК-ТЕСТ ПРОЙДЕН ===")

if __name__ == "__main__":
    if HEADLESS:
        smoke()
    else:
        asyncio.run(main())
