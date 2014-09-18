#!/usr/bin/env python
# -*- coding: utf-8 -*-

# Copyright (c) 2014 Adam Bryzak
# License: MIT

from __future__ import print_function

import datetime
import json
import os
import re
import subprocess
import tempfile
from uuid import uuid4
from contextlib import contextmanager

from flask import Flask, request, abort, make_response

import redis

__all__ = ['app']

r = redis.StrictRedis()

record_validation = {
    'A': lambda d: re.match(r'^(([0-9]|[1-9][0-9]|1[0-9][0-9]|2[0-4][0-9]|25[0-5])\.){3}([0-9]|[1-9][0-9]|1[0-9][0-9]|2[0-4][0-9]|25[0-5])$', d)
}

DEFAULT_DYNAMIC_TTL = '5m'
DEFAULT_STATIC_TTL = '1d'
TTL_REGEX = re.compile(r'^([1-9]w|[1-9]\d?d|[1-9]\d{0,2}[hm]|[1-9]\d{0,8})?$')

@contextmanager
def atomically_write(name):
    dirname = os.path.dirname(name) or '.'
    basename = os.path.basename(name)
    tmpname = None
    try:
        with tempfile.NamedTemporaryFile(delete=False) as f:
            tmpname = f.name
            yield f
        os.rename(tmpname, name)
    finally:
        try:
            if tmpname:
                os.unlink(tmpname)
        except OSError:
            pass

def init_config():
    config = r.hgetall('mydns')
    new_config = dict(config)
    if 'token' not in config:
        token = str(uuid4())
        new_config.setdefault('token', token)
        print('Admin token set to %s' % token)
    new_config.setdefault('zone_file_dir', '.')
    new_config.setdefault('reload_bind', 'false')
    new_config.setdefault('soa_admin', 'admin.my-dns.org')
    new_config.setdefault('name_servers', json.dumps(['ns1.my-dns.org', 'ns2.my-dns.org']))
    if new_config != config:
        r.hmset('mydns', new_config)
init_config()

app = Flask(__name__)

def reload_bind_configuration():
    if r.hget('mydns', 'reload_bind') == 'true':
        with open(os.devnull, 'w') as devnull:
            subprocess.check_call(['/usr/bin/sudo', '/usr/sbin/service', 'bind9', 'reload'], stdout=devnull, stderr=devnull)

def update_zone_file(zone):
    server_config = r.hgetall('mydns')
    zone_file_dir = server_config['zone_file_dir']
    soa_admin = server_config['soa_admin']
    name_servers = json.loads(server_config['name_servers'])
    zone_key = 'mydns:%s' % zone
    zone_config = r.hgetall(zone_key)
    zone_file = os.path.abspath(os.path.join(zone_file_dir, 'db.%s' % zone))
    def t(p):
        zone_config = p.hgetall(zone_key)
        with atomically_write(zone_file) as f:
            zone_ttl = zone_config.get('ttl', DEFAULT_STATIC_TTL)
            serial = int(zone_config.get('serial', '0'))
            new_serial = int('%s00' % datetime.date.today().strftime('%Y%m%d'))
            if new_serial <= serial:
                new_serial = serial + 1
            p.hset(zone_key, 'serial', str(new_serial))
            f.write('$ORIGIN %s.\n' % zone)
            f.write('$TTL %s\n' % zone_ttl)
            f.write('@\t\tIN\tSOA\t{0}. {1}. ( {2} {3} {3} 4w {3} )\n'.format(name_servers[0], soa_admin, new_serial, DEFAULT_DYNAMIC_TTL))
            for name_server in name_servers:
                f.write('@\t\tIN\tNS\t%s.\n' % name_server)
            for label_part_key in p.smembers('%s:labels' % zone_key):
                label, rr_type, rr_key = label_part_key.split(':')
                label_data = p.hgetall('%s:%s' % (zone_key, label_part_key))
                ttl = label_data.get('ttl', '')
                rr_data = label_data['data']
                f.write('%s\t%s\tIN\t%s\t%s\n' % (label, ttl, rr_type, rr_data))
    r.transaction(t, zone_key)
    return zone_file

def add_bind_configuration(zone, zone_file):
    zone_file_dir = r.hget('mydns', 'zone_file_dir')
    bind_conf_file = os.path.join(zone_file_dir, 'named.conf')
    with open(bind_conf_file, 'a', 1) as f:
        f.write('zone "%s" { type master; file "%s"; };\n' % (zone, zone_file))

def normalize_zone(zone):
    zone = zone.lower()
    if not re.match(r'^([a-z][a-z0-9]{0,30}\.){1,4}[a-z]{0,15}$', zone):
        abort(400)
    return zone

def normalize_label(label):
    label = label.lower()
    if not label: label = '@'
    if label == '@': return label
    if not re.match(r'^([a-z][a-z0-9]{0,30}\.){0,2}[a-z][a-z0-9]{0,30}$', label):
        abort(400)
    return label

def json_resp(d):
    resp = make_response(json.dumps(d))
    resp.headers['Content-Type'] = "application/json"
    return resp

@app.route('/api/zones')
def list_zones():
    token = request.form['token']
    if not token: abort(400)
    if token != r.hget('mydns', 'token'): abort(401)
    zones = sorted(r.smembers('mydns:zones'))
    return json_resp({'zones': zones})

@app.route('/api/create-zone', methods=['POST'])
def create_zone():
    token = request.form['token']
    if not token: abort(400)
    zone = normalize_zone(request.form['zone'])
    if token != r.hget('mydns', 'token'): abort(401)
    zone_key = 'mydns:%s' % zone
    zone_token = str(uuid4())
    def t(p):
        p.sadd('mydns:zones', zone)
        p.hsetnx(zone_key, 'token', zone_token)
    if not r.transaction(t)[0]:
        return 'zone exists', 400
    zone_file = update_zone_file(zone)
    add_bind_configuration(zone, zone_file)
    reload_bind_configuration()
    return json_resp({'token': zone_token})

@app.route('/api/update-record', methods=['POST'])
def update_record():
    token = request.form['token']
    if not token: abort(400)
    zone = normalize_zone(request.form['zone'])
    label = normalize_label(request.form.get('label', '', type=str))
    # TODO support all record types and constant values
    rr_type = request.form.get('type', 'A', type=str)
    if rr_type not in record_validation:
        abort(400)
    rr_data = request.form.get('data')
    rr_ttl = ''
    if not rr_data:
        rr_ttl = DEFAULT_DYNAMIC_TTL
        rr_data = request.remote_addr
    if 'ttl' in request.form:
        rr_ttl = request.form['ttl']
    if not record_validation[rr_type](rr_data):
        abort(400)
    if not TTL_REGEX.match(rr_ttl):
        abort(400)
    rr_key = 0 # different keys allow multiple records of the same type for the same label
    zone_key = 'mydns:%s' % zone
    if token != r.hget(zone_key, 'token'): abort(401)
    label_part_key = '%s:%s:%d' % (label, rr_type, rr_key)
    label_key = '%s:%s' % (zone_key, label_part_key)
    def t(p):
        p.sadd('%s:labels' % zone_key, label_part_key)
        p.delete(label_key)
        p.hmset(label_key, {'ttl': rr_ttl, 'data': rr_data})
    r.transaction(t)
    update_zone_file(zone)
    reload_bind_configuration()
    return json_resp({'zone': zone, 'label': label, 'type': rr_type, 'ttl': rr_ttl, 'data': rr_data})

if __name__ == '__main__':
    app.run()
