from connectors.core.connector import Connector, ConnectorError

from .operations import (
    check_health,
    return_bare_dict,
    return_result_set_data,
    return_result_set_result,
)

operations = {
    "return_bare_dict": return_bare_dict,
    "return_result_set_data": return_result_set_data,
    "return_result_set_result": return_result_set_result,
}


class ResultProbeConnector(Connector):
    def execute(self, config, operation, params, **kwargs):
        action = operations.get(operation)
        if action:
            return action(config, params)
        raise ConnectorError(f"Unknown operation: {operation}")

    def check_health(self, config):
        return check_health(config)
