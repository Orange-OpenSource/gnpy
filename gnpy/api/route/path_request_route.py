# coding: utf-8
import http
import json
import os
from pathlib import Path

from flask import request

from gnpy.api import app
from gnpy.api.exception.topology_error import TopologyError
from gnpy.api.service.path_request_service import PathRequestService
from gnpy.tools.json_io import _equipment_from_json, network_from_json
from gnpy.topology.request import ResultElement

PATH_COMPUTATION_BASE_PATH = '/api/v1/path-computation'

_examples_dir = Path(__file__).parent.parent.parent / 'example-data'


@app.route(PATH_COMPUTATION_BASE_PATH, methods=['POST'])
def compute_path(path_request_service: PathRequestService):
    data = request.json
    request_data = data["gnpy-api:request"]
    service = request_data['service']
    if 'topology' in request_data:
        topology = request_data['topology']
    else:
        raise TopologyError('No topology found in request')
    #with open(os.path.join(_examples_dir, 'fake-candi-demo-equipment-corrected.json')) as eqpt_file:
    with open(os.path.join(_examples_dir, 'eqpt_config_openroadm_ver5.json')) as eqpt_file:
        equipment = json.load(eqpt_file)
        equipment = _equipment_from_json(equipment, os.path.join(_examples_dir,'std_medium_gain_advanced_config.json'))
        print(equipment)
    network = network_from_json(topology, equipment)
    propagatedpths, reversed_propagatedpths, rqs, path_computation_id = \
        path_request_service.path_requests_run(service,network,equipment)
    # Generate the output
    result = []
    # assumes that list of rqs and list of propgatedpths have same order
    for i, pth in enumerate(propagatedpths):
        result.append(ResultElement(rqs[i], pth, reversed_propagatedpths[i]))
    return {"result": {"response": [n.json for n in result]}}, 201

