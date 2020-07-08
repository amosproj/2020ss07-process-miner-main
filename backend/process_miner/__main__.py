"""
Main module of the process miner package used to start the process miner.
"""
import logging

import process_miner.research.logs_process_miner as pm
from process_miner import setup_components
from process_miner.research.logs_process_miner import create_results

log = logging.getLogger(__name__)

# embedded, redirect, OAuth, Decoupled, all, not available
APPROACH = "embedded"

# 'error_401_psu_credentials_invalid, 'error_ASPSP_not_found'
# 'error_400_service_invalid_for_step_create_consent',
# 'error_403_consent_invalid', 'error_internal_server'
# 'error_service_unavailable', 'error_400_format'
# 'error_sca_status_405'
ERROR_TYPE = "error_sca_status_405"

# True, False
WITHOUT_ERROR = True


def _main():
    (cfg, retriever, _) = setup_components()
    log.info('starting log retrieval')
    retriever.retrieve_logs()
    global_cfg = cfg.get_section('global')
    miner = pm.Miner(global_cfg['graph_directory'])
    error_dir = pm.Error(global_cfg['error_directory'])
    miner.prepare_graph_dir()
    error_dir.prepare_graph_dir()
    create_results(WITHOUT_ERROR, APPROACH, ERROR_TYPE)


if __name__ == '__main__':
    _main()
