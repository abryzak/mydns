from __future__ import print_function

import re
import json
from uuid import uuid4

from flask import Flask, request, abort, make_response

import redis

r = redis.StrictRedis()

def init_config():
    config = r.hgetall('mydns')
    new_config = dict(config)
    if 'token' not in config:
        token = str(uuid4())
        new_config.setdefault('token', token)
        print('Admin token set to %s' % token)
    if new_config != config:
        r.hmset('mydns', new_config)
init_config()

app = Flask(__name__)

def update_zone_file(zone):
    pass

def normalize_zone(zone):
    zone = zone.lower()
    if not re.match(r'^([a-z][a-z0-9]{0,31}\.)+[a-z]{0,16}$', zone):
        abort(400)
    return zone

def normalize_label(label):
    label = label.lower()
    if not label: label = '@'
    if label == '@': return label
    if not re.match(r'^([a-z][a-z0-9]{0,31}\.)*[a-z][a-z0-9]{0,31}$', label):
        abort(400)
    return label

def json_resp(d):
    resp = make_response(json.dumps(d))
    resp.headers['Content-Type'] = "application/json"
    return resp

@app.route('/api/create-zone', methods=['POST'])
def create_zone():
    token = request.form['token']
    if not token: abort(400)
    zone = normalize_zone(request.form['zone'])
    if token != r.hget('mydns', 'token'): abort(401)
    zone_key = 'mydns:%s' % zone
    zone_token = str(uuid4())
    if not r.hsetnx(zone_key, 'token', zone_token):
        return 'zone exists', 400
    update_zone_file(zone)
    return json_resp({'token': zone_token})

@app.route('/api/update-record', methods=['POST'])
def update_record():
    token = request.form['token']
    if not token: abort(400)
    zone = normalize_zone(request.form['zone'])
    label = normalize_label(request.form.get('label', '', str))
    # TODO support all record types and constant values
    rr_type = 'A'
    rr_ttl = 60*5 # 5 minutes
    rr_data = request.remote_addr
    rr_key = 0 # different keys allow multiple records of the same type for the same label
    zone_key = 'mydns:%s' % zone
    if token != r.hget(zone_key, 'token'): abort(401)
    label_key = '%s:%s:%s:%d' % (zone_key, label, rr_type, rr_key)
    def t(p):
        p.delete(label_key)
        p.hmset(label_key, {'ttl': rr_ttl, 'data': rr_data})
    r.transaction(t)
    update_zone_file(zone)
    return json_resp({})

if __name__ == '__main__':
    app.run()
