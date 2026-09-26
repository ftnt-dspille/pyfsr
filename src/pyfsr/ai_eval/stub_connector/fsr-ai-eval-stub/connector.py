"""FSR AI Eval Stub connector: lifecycle hooks start/stop the stub MCP listener (see operations.py)."""

from connectors.core.connector import Connector, ConnectorError

from .operations import OPERATIONS, start_listener, stop_listener


class EvalStub(Connector):
    def execute(self, config, operation, params, **kwargs):
        action = OPERATIONS.get(operation)
        if action is None:
            raise ConnectorError(f"unsupported operation {operation}")
        return action(config, params or {})

    def check_health(self, config):
        start_listener(config)  # (re)start after a reboot/service restart
        return True

    def on_add_config(self, config, active):
        if active:
            start_listener(config)

    def on_update_config(self, old_config, new_config, active):
        stop_listener(old_config)
        if active:
            start_listener(new_config)

    def on_activate(self, config):
        start_listener(config)

    def on_deactivate(self, config):
        stop_listener(config)

    def on_delete_config(self, config):
        stop_listener(config)
