#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
space_game.py — играбельный прототип космического боя (pygame).

Запуск:
    pip install pygame
    python space_game.py

Смоук-тест без окна (проверка боевой логики):
    python space_game.py --smoke

Управление:
    мышь   — прицел (нос корабля доворачивается на курсор)
    W / S  — тяга больше / меньше
    ЛКМ    — огонь выбранным оружием
    1..4   — выбор оружия: рельсотрон / лазеры / атомные ракеты / плазмопушка
    R      — удерживать 3 с для самоликвидации
    ESC    — выход
    R после гибели — рестарт
"""

import math
import random
import sys

try:
    import pygame
except ImportError:
    pygame = None

# ---------------- КОНСТАНТЫ ----------------
TICK = 1.0 / 60.0
ARENA = 6000.0            # арена, м
WALL_MARGIN = 120.0       # мягкая граница
SCALE = 0.15              # пикселей на метр

CAPACITOR = 300.0         # ёмкость конденсатора, EU
OVERLOAD_LIMIT = 500.0    # потолок накопления, EU
OVERHEAT_FRAC = 0.9       # порог перегрева от потолка
SD_HOLD = 3.0             # удержание кнопки самоликвидации, с
CONE = 0.09               # полуугол захвата лазера, рад

SHIP_TYPES = {
    'cruiser':    dict(hp=1000.0, thrust=120.0, vmax=220.0, turn=2.0, cost=10.0, reactor=100.0, r=22.0),
    'fighter':    dict(hp=350.0,  thrust=200.0, vmax=320.0, turn=3.5, cost=8.0,  reactor=100.0, r=12.0),
    'raider':     dict(hp=600.0,  thrust=260.0, vmax=380.0, turn=3.0, cost=10.0, reactor=100.0, r=15.0),
    'battleship': dict(hp=3500.0, thrust=40.0,  vmax=90.0,  turn=0.8, cost=15.0, reactor=150.0, r=40.0),
}

WEAPONS = {
    'railgun': dict(dmg=35.0,  spd=900.0, rof=3.0,  cost=0.0,   range=None),
    'laser':   dict(dmg=18.0,  spd=None,  rof=5.0,  cost=25.0,  range=1200.0),
    'rocket':  dict(dmg=400.0, spd=320.0, rof=0.25, cost=0.0,   range=None,
                     aoe=150.0, turn=2.0, proj_hp=30.0),
    'plasma':  dict(dmg=350.0, spd=350.0, rof=1/3,  cost=120.0, range=None, r=12.0),
}


def norm_ang(a):
    while a > math.pi:
        a -= 2 * math.pi
    while a < -math.pi:
        a += 2 * math.pi
    return a


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


# ---------------- СНАРЯДЫ ----------------
class Proj:
    def __init__(self, kind, x, y, vx, vy, dmg, owner, life, r=3.0, aoe=0.0,
                 target=None, proj_hp=0.0):
        self.kind = kind
        self.x, self.y = x, y
        self.vx, self.vy = vx, vy
        self.dmg = dmg
        self.owner = owner
        self.life = life
        self.r = r
        self.aoe = aoe
        self.target = target
        self.hp = proj_hp
        self.dead = False

    def update(self, dt):
        if self.kind == 'rocket' and self.target is not None and not self.target.dead:
            want = math.atan2(self.target.y - self.y, self.target.x - self.x)
            cur = math.atan2(self.vy, self.vx)
            diff = norm_ang(want - cur)
            mx = WEAPONS['rocket']['turn'] * dt
            ang = cur + clamp(diff, -mx, mx)
            self.vx = math.cos(ang) * WEAPONS['rocket']['spd']
            self.vy = math.sin(ang) * WEAPONS['rocket']['spd']
        self.x += self.vx * dt
        self.y += self.vy * dt
        self.life -= dt
        if self.life <= 0:
            self.dead = True


# ---------------- КОРАБЛЬ ----------------
class Ship:
    def __init__(self, stype, x, y, ang, team, is_player=False, hp_scale=1.0):
        c = SHIP_TYPES[stype]
        self.stype = stype
        self.cfg = c
        self.x, self.y = x, y
        self.vx = self.vy = 0.0
        self.ang = ang
        self.desired_ang = ang
        self.maxhp = c['hp'] * hp_scale
        self.hp = self.maxhp
        self.team = team
        self.is_player = is_player
        self.cap = CAPACITOR
        self.throttle = 0.0
        self.overheated = False
        self.ammo = self._init_ammo()
        self.cool = {k: 0.0 for k in WEAPONS}
        self.weapons = self._init_weapons()
        self.wsel = 0
        self.firing = False
        self.sd_requested = False
        self.hold_sd = 0.0
        self.flee_until = 0.0
        self.flee_used = False
        self.orbit_dir = 1 if random.random() < 0.5 else -1
        self.orbit_t = random.uniform(4.0, 8.0)
        self.dash_t = 0.0
        self.dash_cd = random.uniform(2.0, 5.0)
        self.dead = False

    def _init_weapons(self):
        return {
            'cruiser':    ['railgun', 'laser', 'rocket', 'plasma'],
            'fighter':    ['railgun', 'laser'],
            'raider':     ['laser', 'rocket'],
            'battleship': ['railgun', 'plasma'],
        }[self.stype]

    def _init_ammo(self):
        return {
            'cruiser':    {'railgun': 200, 'rocket': 8},
            'fighter':    {'railgun': 120},
            'raider':     {'rocket': 4},
            'battleship': {'railgun': 400},
        }[self.stype]

    # ----- движение -----
    def update(self, dt, world):
        if self.dead:
            return
        # поворот к желаемому курсу
        diff = norm_ang(self.desired_ang - self.ang)
        mx = self.cfg['turn'] * dt
        self.ang += clamp(diff, -mx, mx)

        # перегрев: наказание за верхний предел конденсатора
        if (not self.overheated and self.throttle > 0.5
                and self.cap >= OVERLOAD_LIMIT * OVERHEAT_FRAC):
            self.overheated = True
        if self.overheated and self.cap < OVERLOAD_LIMIT * 0.5:
            self.overheated = False

        thr = self.throttle
        if self.overheated:
            thr = min(thr, 0.25)
        cost = self.cfg['cost'] * (1.0 + 2.2 * thr * thr)
        if self.cap < cost * dt:
            thr = 0.0  # конденсатор пуст — двигатель глохнет

        spd = math.hypot(self.vx, self.vy)
        if spd < self.cfg['vmax']:
            accel = self.cfg['thrust'] * (1.0 - spd / self.cfg['vmax'])
            self.vx += math.cos(self.ang) * accel * thr * dt
            self.vy += math.sin(self.ang) * accel * thr * dt
        self.cap = clamp(self.cap + (self.cfg['reactor'] - cost) * dt, 0.0, OVERLOAD_LIMIT)

        self.x += self.vx * dt
        self.y += self.vy * dt

        lim = ARENA / 2.0 - WALL_MARGIN
        if abs(self.x) > lim or abs(self.y) > lim:
            self.x = clamp(self.x, -lim, lim)
            self.y = clamp(self.y, -lim, lim)
            self.vx *= 0.9
            self.vy *= 0.9

        for k in self.cool:
            self.cool[k] = max(0.0, self.cool[k] - dt)

        if self.firing and self.weapons:
            self.fire_one(self.weapons[self.wsel], world)

        # самоликвидация
        if self.sd_requested and not self.dead:
            self.hold_sd += dt
            if self.hold_sd >= SD_HOLD:
                self.self_destruct(world)
        else:
            self.hold_sd = 0.0

    # ----- оружие -----
    def fire_one(self, wname, world):
        w = WEAPONS[wname]
        if self.dead or self.cool[wname] > 0.0:
            return
        if wname in self.ammo and self.ammo[wname] <= 0:
            return
        if wname == 'laser':
            per = w['cost'] / w['rof']       # 5 EU на импульс
            if self.cap < per:
                return
            self.cap -= per
        else:
            if self.cap < w['cost']:
                return
            self.cap -= w['cost']
        self.cool[wname] = 1.0 / w['rof']
        if wname in self.ammo:
            self.ammo[wname] -= 1

        dx = self.x + math.cos(self.ang) * self.cfg['r']
        dy = self.y + math.sin(self.ang) * self.cfg['r']

        if wname == 'laser':
            tgt = None
            bd = 1e18
            for o in world.ships:
                if o.dead or o.team == self.team:
                    continue
                d = math.hypot(o.x - self.x, o.y - self.y)
                if d > w['range']:
                    continue
                a = math.atan2(o.y - self.y, o.x - self.x)
                if abs(norm_ang(a - self.ang)) <= CONE:
                    if d < bd:
                        bd = d
                        tgt = o
            if tgt is not None:
                tgt.take_damage(w['dmg'], world)
                world.beams.append([self.x, self.y, tgt.x, tgt.y, 0.1, (255, 80, 200)])
            else:
                world.beams.append([
                    self.x, self.y,
                    self.x + math.cos(self.ang) * w['range'],
                    self.y + math.sin(self.ang) * w['range'],
                    0.08, (120, 60, 160)])
        elif wname == 'railgun':
            world.projs.append(Proj('bullet', dx, dy,
                                    self.vx + math.cos(self.ang) * w['spd'],
                                    self.vy + math.sin(self.ang) * w['spd'],
                                    w['dmg'], self, life=2.0, r=3.0))
        elif wname == 'rocket':
            off = random.uniform(-0.2, 0.2)
            a = self.ang + off
            world.projs.append(Proj('rocket', dx, dy,
                                    self.vx + math.cos(a) * w['spd'],
                                    self.vy + math.sin(a) * w['spd'],
                                    w['dmg'], self, life=6.0, r=5.0,
                                    aoe=w['aoe'],
                                    target=world.nearest_enemy(self),
                                    proj_hp=w['proj_hp']))
        elif wname == 'plasma':
            world.projs.append(Proj('plasma', dx, dy,
                                    self.vx + math.cos(self.ang) * w['spd'],
                                    self.vy + math.sin(self.ang) * w['spd'],
                                    w['dmg'], self, life=10.0, r=w['r']))

    # ----- самоликвидация -----
    def self_destruct(self, world):
        if self.dead:
            return
        base = self.cap * 2.0
        radius = 300.0 + self.cap
        world.explode(self.x, self.y, base, radius)
        world.booms.append([self.x, self.y, radius, 1.2])
        self.hold_sd = 0.0
        self.take_damage(1e9, world)

    # ----- урон и гибель -----
    def take_damage(self, amt, world):
        if self.dead:
            return
        self.hp -= amt
        if self.hp <= 0:
            self.hp = 0
            self.die(world)

    def die(self, world):
        if self.dead:
            return
        self.dead = True
        world.booms.append([self.x, self.y, 120.0 + self.maxhp / 20.0, 0.8])
        if self.team == 'red':
            world.kills += 1
        self.drop_loot(world)

    def drop_loot(self, world):
        if self.stype == 'battleship':
            world.loots.append(dict(kind='ammo', x=self.x, y=self.y, ttl=15.0))
            world.loots.append(dict(kind='rocket', x=self.x + 40, y=self.y, ttl=15.0))
            world.loots.append(dict(kind='energy', x=self.x - 40, y=self.y, ttl=15.0))
        elif random.random() < 0.6:
            world.loots.append(dict(kind=random.choice(['ammo', 'rocket', 'energy']),
                                    x=self.x, y=self.y, ttl=15.0))

    # ----- ИИ -----
    def sd_threat_check(self, world):
        for o in world.ships:
            if o.dead or o.team == self.team:
                continue
            if o.hold_sd > 1.5 and math.hypot(o.x - self.x, o.y - self.y) < 2000:
                return True
        return False

    def flee_from(self, tgt):
        self.desired_ang = math.atan2(self.y - tgt.y, self.x - tgt.x)
        self.throttle = 1.0
        self.firing = False

    def ai_update(self, world, now):
        if self.dead:
            return
        st = self.stype
        if st == 'cruiser':
            self.ai_cruiser(world, now)
        elif st == 'fighter':
            self.ai_fighter(world, now)
        elif st == 'raider':
            self.ai_raider(world, now)
        else:
            self.ai_battleship(world, now)

    def ai_cruiser(self, world, now):
        tgt = world.nearest_enemy(self)
        if tgt is None:
            self.throttle = 0.2
            return
        dx, dy = tgt.x - self.x, tgt.y - self.y
        d = math.hypot(dx, dy)
        to = math.atan2(dy, dx)
        if self.sd_threat_check(world):
            self.flee_from(tgt)
            return
        self.orbit_t -= TICK
        if self.orbit_t <= 0:
            self.orbit_dir *= -1
            self.orbit_t = random.uniform(4.0, 8.0)
        if d > 600:
            self.desired_ang = to
            self.throttle = 1.0
        else:
            self.desired_ang = to + math.pi / 2 * self.orbit_dir
            self.throttle = 0.6
        aligned = abs(norm_ang(to - self.ang)) < 0.12
        if 200 < d < 1600 and self.ammo.get('rocket', 0) > 0:
            self.fire_one('rocket', world)
        if aligned and d < 700:
            self.fire_one('railgun', world)
        if aligned and d < 350:
            self.fire_one('laser', world)
        if aligned and d < 800 and self.cap >= WEAPONS['plasma']['cost']:
            self.fire_one('plasma', world)

    def ai_fighter(self, world, now):
        tgt = world.nearest_enemy(self)
        if tgt is None:
            self.throttle = 0.3
            return
        dx, dy = tgt.x - self.x, tgt.y - self.y
        d = math.hypot(dx, dy)
        to = math.atan2(dy, dx)
        if self.sd_threat_check(world):
            self.flee_from(tgt)
            return
        if not self.flee_used and self.hp < 0.3 * self.maxhp:
            self.flee_used = True
            self.flee_until = now + 5.0
        if now < self.flee_until:
            self.flee_from(tgt)
            return
        if d > 450:
            self.desired_ang = to
            self.throttle = 1.0
            self.firing = False
        else:
            self.orbit_t -= TICK
            if self.orbit_t <= 0:
                self.orbit_dir *= -1
                self.orbit_t = random.uniform(4.0, 8.0)
            self.desired_ang = to + math.pi / 2 * self.orbit_dir
            self.throttle = 0.7
            aligned = abs(norm_ang(to - self.ang)) < 0.12
            self.firing = aligned and d < 700
            if aligned and d < 400:
                self.fire_one('laser', world)
        if d > 450:
            aligned = abs(norm_ang(to - self.ang)) < 0.12
            self.firing = aligned and d < 900

    def ai_raider(self, world, now):
        tgt = world.nearest_enemy(self)
        if tgt is None:
            self.throttle = 0.3
            return
        dx, dy = tgt.x - self.x, tgt.y - self.y
        d = math.hypot(dx, dy)
        to = math.atan2(dy, dx)
        if self.sd_threat_check(world):
            self.flee_from(tgt)
            return
        self.dash_cd -= TICK
        self.dash_t -= TICK
        if self.dash_cd <= 0 and d < 700:
            self.dash_t = 1.0
            self.dash_cd = 6.0
        dry = self.ammo.get('rocket', 0) <= 0
        far, near = (1200.0, 800.0) if not dry else (600.0, 400.0)
        if d > far:
            self.desired_ang = to
            self.throttle = 1.0
        elif d < near:
            self.desired_ang = to + math.pi
            self.throttle = 1.0
        elif self.dash_t > 0:
            self.desired_ang = to + math.pi / 2 * self.orbit_dir
            self.throttle = 1.0
        else:
            self.desired_ang = to + math.pi / 2 * self.orbit_dir
            self.throttle = 0.5
        aligned = abs(norm_ang(to - self.ang)) < 0.25
        if aligned and 500 < d < 1600 and self.ammo.get('rocket', 0) > 0:
            self.fire_one('rocket', world)
        if d < 600:
            self.desired_ang = to
            if abs(norm_ang(to - self.ang)) < CONE:
                self.fire_one('laser', world)

    def ai_battleship(self, world, now):
        tgt = world.nearest_enemy(self)
        if tgt is None:
            self.throttle = 0.0
            return
        dx, dy = tgt.x - self.x, tgt.y - self.y
        d = math.hypot(dx, dy)
        # упреждение по скорости цели x0.7 — плазму можно уворачивать
        t_arr = d / WEAPONS['plasma']['spd']
        ax = tgt.x + tgt.vx * t_arr * 0.7
        ay = tgt.y + tgt.vy * t_arr * 0.7
        self.desired_ang = math.atan2(ay - self.y, ax - self.x)
        self.throttle = 0.0
        if abs(norm_ang(self.desired_ang - self.ang)) < 0.2:
            self.fire_one('plasma', world)
        if d < 900 and abs(norm_ang(math.atan2(dy, dx) - self.ang)) < 0.12:
            self.fire_one('railgun', world)


# ---------------- МИР ----------------
class World:
    def __init__(self):
        self.ships = []
        self.projs = []
        self.loots = []
        self.beams = []
        self.booms = []
        self.wave = 0
        self.kills = 0
        self.break_t = 0.0
        self.msg = ''
        self.msg_t = 0.0
        self.time = 0.0
        self.player = None
        self.player_ai = False
        self.stars = [(random.uniform(-3000, 3000), random.uniform(-3000, 3000))
                      for _ in range(260)]

    def nearest_enemy(self, s):
        best, bd = None, 1e18
        for o in self.ships:
            if o.dead or o.team == s.team:
                continue
            d = (o.x - s.x) ** 2 + (o.y - s.y) ** 2
            if d < bd:
                bd, best = d, o
        return best

    def explode(self, x, y, base, radius):
        for s in self.ships:
            if s.dead:
                continue
            d = math.hypot(s.x - x, s.y - y)
            if d <= radius:
                s.take_damage(base * (1.0 - 0.75 * d / radius), self)
        self.booms.append([x, y, radius, 0.6])

    def wave_comp(self, n):
        base = [
            ['fighter'] * 3,
            ['fighter'] * 5,
            ['fighter'] * 7 + ['raider'],
            ['fighter'] * 6 + ['raider'] * 2,
            ['fighter'] * 10 + ['raider'] * 3,
            ['raider'] * 4 + ['battleship'],
        ]
        if n <= 6:
            return list(base[n - 1])
        comp = ['fighter'] * (8 + n) + ['raider'] * (2 + (n - 6))
        if n % 2 == 0:
            comp.append('battleship')
        return comp

    def start_wave(self, n):
        self.wave = n
        comp = self.wave_comp(n)
        hp_scale = 1.0 + 0.1 * max(0, n - 6)
        px = self.player.x if self.player else 0.0
        py = self.player.y if self.player else 0.0
        for i, st in enumerate(comp):
            a = 2 * math.pi * i / max(1, len(comp)) + random.uniform(-0.3, 0.3)
            R = random.uniform(2200.0, 2600.0)
            x = clamp(px + math.cos(a) * R, -2600, 2600)
            y = clamp(py + math.sin(a) * R, -2600, 2600)
            self.ships.append(Ship(st, x, y, a + math.pi, 'red', hp_scale=hp_scale))
        self.set_msg('ВОЛНА %d' % n, 4.0)

    def set_msg(self, text, ttl):
        self.msg = text
        self.msg_t = ttl

    def update(self, dt):
        self.time += dt
        now = self.time

        for s in self.ships:
            s.sd_requested = False if s.is_player else s.sd_requested

        for s in self.ships:
            if s.dead:
                continue
            if s is not self.player or self.player_ai:
                s.ai_update(self, now)
            s.update(dt, self)

        # снаряды
        for p in self.projs:
            p.update(dt)
            if p.dead:
                continue
            # попадания в корабли
            for s in self.ships:
                if s.dead or s.team == p.owner.team:
                    continue
                if math.hypot(s.x - p.x, s.y - p.y) < s.cfg['r'] + p.r + math.hypot(p.vx, p.vy) * dt * 0.5:
                    if p.aoe > 0:
                        self.explode(p.x, p.y, p.dmg, p.aoe)
                    else:
                        s.take_damage(p.dmg, self)
                    p.dead = True
                    break
            if p.dead:
                continue
            # перехват ракет
            if p.kind in ('bullet', 'plasma'):
                for q in self.projs:
                    if q.kind != 'rocket' or q.dead or q.owner.team == p.owner.team:
                        continue
                    if math.hypot(q.x - p.x, q.y - p.y) < q.r + p.r + 4:
                        q.hp -= p.dmg
                        p.dead = True
                        if q.hp <= 0:
                            q.dead = True
                            self.booms.append([q.x, q.y, 60.0, 0.4])
                        break
        self.projs = [p for p in self.projs if not p.dead]

        # таран
        alive = [s for s in self.ships if not s.dead]
        for i in range(len(alive)):
            for j in range(i + 1, len(alive)):
                a, b = alive[i], alive[j]
                d = math.hypot(a.x - b.x, a.y - b.y)
                if d < a.cfg['r'] + b.cfg['r']:
                    rel = math.hypot(a.vx - b.vx, a.vy - b.vy)
                    if rel > 50:
                        a.take_damage(rel * 0.5, self)
                        b.take_damage(rel * 0.5, self)
                    nx, ny = (b.x - a.x) / max(d, 1e-6), (b.y - a.y) / max(d, 1e-6)
                    push = 30.0
                    a.x -= nx * push * dt * 60 * 0.05
                    a.y -= ny * push * dt * 60 * 0.05
                    b.x += nx * push * dt * 60 * 0.05
                    b.y += ny * push * dt * 60 * 0.05

        # лут
        for l in self.loots:
            l['ttl'] -= dt
            if l['ttl'] <= 0:
                continue
            for s in alive:
                if math.hypot(s.x - l['x'], s.y - l['y']) < 60:
                    if l['kind'] == 'ammo':
                        s.ammo['railgun'] = s.ammo.get('railgun', 0) + 40
                    elif l['kind'] == 'rocket':
                        s.ammo['rocket'] = s.ammo.get('rocket', 0) + 1
                    else:
                        s.cap = min(OVERLOAD_LIMIT, s.cap + 100.0)
                    l['ttl'] = 0
                    break
        self.loots = [l for l in self.loots if l['ttl'] > 0]

        # визуал
        for b in self.beams:
            b[4] -= dt
        self.beams = [b for b in self.beams if b[4] > 0]
        for b in self.booms:
            b[3] -= dt
        self.booms = [b for b in self.booms if b[3] > 0]
        if self.msg_t > 0:
            self.msg_t -= dt

        # волны
        if self.wave > 0 and not any((not s.dead and s.team == 'red') for s in self.ships):
            if self.break_t <= 0:
                self.break_t = 15.0
                self.set_msg('ВОЛНА ЗАЧИЩЕНА — сбор лута 15 с', 15.0)
        if self.break_t > 0:
            self.break_t -= dt
            if self.break_t <= 0:
                self.start_wave(self.wave + 1)

        self.ships = [s for s in self.ships if not s.dead]


# ---------------- СМОУК-ТЕСТ ----------------
def smoke():
    random.seed(7)
    print('=== Тест самоликвидации (конденсатор 500 EU) ===')
    w = World()
    boom = Ship('cruiser', 0.0, 0.0, 0.0, 'blue')
    boom.cap = OVERLOAD_LIMIT
    f = Ship('fighter', 100.0, 0.0, math.pi, 'red')
    r = Ship('raider', 550.0, 0.0, math.pi, 'red')
    b = Ship('battleship', 720.0, 0.0, math.pi, 'red')
    w.ships += [boom, f, r, b]
    boom.self_destruct(w)
    print('истребитель (100 м):  hp=%s -> %s' % (350, 'УБИТ' if f.dead else int(f.hp)))
    print('рейдер     (550 м):  hp=%s -> %s' % (600, 'УБИТ' if r.dead else int(r.hp)))
    print('линкор     (720 м):  урон=%d из 3500 (%.0f%%)' % (3500 - b.hp, 100 * (3500 - b.hp) / 3500))

    print('\n=== Дуэли (крейсер-бот против каждого типа) ===')
    for enemy in ('fighter', 'raider', 'battleship'):
        w = World()
        w.player_ai = True
        a = Ship('cruiser', -400.0, 0.0, 0.0, 'blue')
        b = Ship(enemy, 400.0, 0.0, math.pi, 'red')
        w.ships += [a, b]
        t = 0.0
        while t < 90.0 and not (a.dead or b.dead):
            w.update(TICK)
            t += TICK
        res = 'крейсер' if b.dead else ('противник' if a.dead else 'ничья за 90 с')
        print('крейсер vs %-10s: победа %s за %4.1f с (hp крейсера %d, hp врага %d)'
              % (enemy, res, t, int(a.hp), int(b.hp)))

    print('\n=== Выживание: 3 волны под ИИ ===')
    w = World()
    w.player_ai = True
    p = Ship('cruiser', 0.0, 0.0, -math.pi / 2, 'blue', is_player=True)
    w.player = p
    w.ships.append(p)
    w.start_wave(1)
    t = 0.0
    while t < 180.0:
        w.update(TICK)
        t += TICK
        if w.wave >= 4:
            break
    print('достигнута волна %d, убито %d, hp игрока %d/%d, снарядов %d'
          % (w.wave, w.kills, int(p.hp), int(p.maxhp), p.ammo.get('railgun', 0)))
    print('\nСмоук пройден.')


# ---------------- ОТОБРАЖЕНИЕ ----------------
SHIP_SHAPES = {
    'cruiser':    [(30, 0), (-20, 14), (-12, 0), (-20, -14)],
    'fighter':    [(14, 0), (-9, 7), (-9, -7)],
    'raider':     [(18, 0), (-6, 10), (-12, 0), (-6, -10)],
    'battleship': [(44, 0), (-26, 24), (-30, 0), (-26, -24)],
}
TEAM_COLORS = {'blue': (90, 200, 255), 'red': (235, 80, 80)}
LOOT_COLORS = {'ammo': (255, 220, 120), 'rocket': (255, 140, 60), 'energy': (120, 255, 160)}


def draw_world(screen, world, player, font, big, SW, SH):
    screen.fill((8, 10, 16))
    cx, cy = player.x, player.y

    def w2s(x, y):
        return int((x - cx) * SCALE + SW / 2), int((y - cy) * SCALE + SH / 2)

    for sx, sy in world.stars:
        p = w2s(sx, sy)
        if 0 <= p[0] < SW and 0 <= p[1] < SH:
            screen.set_at(p, (60, 65, 85))

    # граница арены
    pts = [w2s(-ARENA / 2, -ARENA / 2), w2s(ARENA / 2, -ARENA / 2),
           w2s(ARENA / 2, ARENA / 2), w2s(-ARENA / 2, ARENA / 2)]
    pygame.draw.polygon(screen, (45, 55, 75), pts, 2)

    for l in world.loots:
        p = w2s(l['x'], l['y'])
        pygame.draw.rect(screen, LOOT_COLORS[l['kind']], (p[0] - 4, p[1] - 4, 8, 8))

    for p in world.projs:
        pos = w2s(p.x, p.y)
        if p.kind == 'bullet':
            pygame.draw.circle(screen, (255, 220, 100), pos, 2)
        elif p.kind == 'plasma':
            pygame.draw.circle(screen, (120, 220, 255), pos, max(2, int(p.r * SCALE)))
        elif p.kind == 'rocket':
            pygame.draw.circle(screen, (255, 150, 60), pos, 3)
            tail = w2s(p.x - p.vx * 0.1, p.y - p.vy * 0.1)
            pygame.draw.line(screen, (180, 90, 40), pos, tail, 1)

    for b in world.beams:
        a, c = w2s(b[0], b[1]), w2s(b[2], b[3])
        pygame.draw.line(screen, b[5], a, c, 2)

    for bx, by, rad, _ in world.booms:
        p = w2s(bx, by)
        r = int(rad * SCALE)
        pygame.draw.circle(screen, (255, 180, 60), p, max(2, r), 2)
        if r > 4:
            pygame.draw.circle(screen, (255, 240, 180), p, max(2, int(r * 0.5)), 1)

    for s in world.ships:
        col = TEAM_COLORS[s.team]
        pts = []
        for lx, ly in SHIP_SHAPES[s.stype]:
            wx = s.x + lx * math.cos(s.ang) - ly * math.sin(s.ang)
            wy = s.y + lx * math.sin(s.ang) + ly * math.cos(s.ang)
            pts.append(w2s(wx, wy))
        pygame.draw.polygon(screen, col, pts, 2 if not s.is_player else 0)
        if s.is_player:
            pygame.draw.polygon(screen, (200, 255, 255), pts, 1)
        # полоска HP
        top = w2s(s.x, s.y - s.cfg['r'] - 15)[1]
        left = w2s(s.x, s.y)[0] - int(s.cfg['r'] * SCALE) - 4
        wbar = int(s.cfg['r'] * 2 * SCALE) + 8
        pygame.draw.rect(screen, (70, 20, 20), (left, top, wbar, 3))
        pygame.draw.rect(screen, (80, 220, 90),
                         (left, top, int(wbar * s.hp / s.maxhp), 3))
        # индикатор самоликвидации
        if s.hold_sd > 0:
            pygame.draw.rect(screen, (255, 60, 60),
                             (left, top - 6, int(wbar * s.hold_sd / SD_HOLD), 2))

    # --- HUD ---
    pygame.draw.rect(screen, (30, 30, 40), (10, 10, 260, 10))
    pygame.draw.rect(screen, (80, 220, 90),
                     (10, 10, int(260 * player.hp / player.maxhp), 10))
    pygame.draw.rect(screen, (30, 30, 40), (10, 26, 260, 10))
    pygame.draw.rect(screen, (255, 210, 80),
                     (10, 26, int(260 * player.cap / OVERLOAD_LIMIT), 10))
    hud = [
        'HP %d/%d' % (int(player.hp), int(player.maxhp)),
        'Энергия %d/%d EU' % (int(player.cap), int(OVERLOAD_LIMIT)),
    ]
    for i, line in enumerate(hud):
        screen.blit(font.render(line, True, (220, 220, 220)), (10, 40 + i * 20))
    wnames = {'railgun': 'Рельсотрон', 'laser': 'Лазеры',
              'rocket': 'Ракеты', 'plasma': 'Плазма'}
    for i, wn in enumerate(player.weapons):
        sel = '>' if i == player.wsel else ' '
        ammo = player.ammo.get(wn)
        cnt = '' if ammo is None else ' x%d' % ammo
        screen.blit(font.render('%s %d %s%s' % (sel, i + 1, wnames[wn], cnt),
                                True, (255, 230, 120) if i == player.wsel else (150, 150, 160)),
                    (10, 86 + i * 20))
    enemies = sum(1 for s in world.ships if s.team == 'red')
    screen.blit(font.render('Волна %d   Врагов: %d   Убито: %d'
                            % (world.wave, enemies, world.kills), True, (200, 200, 220)),
                (SW - 280, 12))
    if player.overheated:
        screen.blit(font.render('ПЕРЕГРЕВ — тяга ограничена', True, (255, 120, 60)), (10, 170))
    if world.msg_t > 0:
        surf = big.render(world.msg, True, (255, 240, 200))
        screen.blit(surf, (SW // 2 - surf.get_width() // 2, 60))
    if player.hold_sd > 0:
        txt = 'САМОЛИКВИДАЦИЯ %d%%' % int(100 * player.hold_sd / SD_HOLD)
        surf = font.render(txt, True, (255, 60, 60))
        screen.blit(surf, (SW // 2 - surf.get_width() // 2, SH - 60))
        pygame.draw.rect(screen, (60, 20, 20), (SW // 2 - 120, SH - 40, 240, 8))
        pygame.draw.rect(screen, (255, 60, 60),
                         (SW // 2 - 120, SH - 40, int(240 * player.hold_sd / SD_HOLD), 8))
    if player.dead:
        surf = big.render('КОРАБЛЬ УНИЧТОЖЕН — R: рестарт', True, (255, 90, 90))
        screen.blit(surf, (SW // 2 - surf.get_width() // 2, SH // 2))


# ---------------- ЗАПУСК ----------------
def main():
    if pygame is None:
        print('Нужен pygame:  pip install pygame')
        return
    pygame.init()
    SW, SH = 1100, 720
    screen = pygame.display.set_mode((SW, SH))
    pygame.display.set_caption('Space Prototype — крейсер')
    clock = pygame.time.Clock()
    font = pygame.font.Font(None, 24)
    big = pygame.font.Font(None, 48)

    def new_game():
        w = World()
        p = Ship('cruiser', 0.0, 0.0, -math.pi / 2, 'blue', is_player=True)
        w.player = p
        w.ships.append(p)
        w.start_wave(1)
        return w, p

    world, player = new_game()
    while True:
        dt = clock.tick(60) / 1000.0
        restart = False
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                pygame.quit()
                return
            if e.type == pygame.KEYDOWN:
                if e.key == pygame.K_ESCAPE:
                    pygame.quit()
                    return
                if pygame.K_1 <= e.key <= pygame.K_4:
                    idx = e.key - pygame.K_1
                    if idx < len(player.weapons):
                        player.wsel = idx
                if e.key == pygame.K_r and player.dead:
                    restart = True
        if restart:
            world, player = new_game()

        keys = pygame.key.get_pressed()
        mx, my = pygame.mouse.get_pos()
        wx = player.x + (mx - SW / 2) / SCALE
        wy = player.y + (my - SH / 2) / SCALE
        player.desired_ang = math.atan2(wy - player.y, wx - player.x)
        if keys[pygame.K_w]:
            player.throttle = min(1.0, player.throttle + 0.8 * dt)
        if keys[pygame.K_s]:
            player.throttle = max(0.0, player.throttle - 1.2 * dt)
        player.firing = pygame.mouse.get_pressed()[0] and not player.dead
        player.sd_requested = bool(keys[pygame.K_r]) and not player.dead

        world.update(dt)
        draw_world(screen, world, player, font, big, SW, SH)
        pygame.display.flip()


if __name__ == '__main__':
    if '--smoke' in sys.argv:
        smoke()
    else:
        main()
