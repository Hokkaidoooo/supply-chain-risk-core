"""固定分层合成图；内存与耗时只用于本机基线，不代表业务吞吐。"""
import json
import platform
import tracemalloc
import networkx as nx
from supply_chain_risk_core import exposure_report

graph = nx.DiGraph()
layers = [['root']] + [[f'{level}-{index}' for index in range(5)] for level in range(1, 6)]
for previous, current in zip(layers, layers[1:]):
    for source in previous:
        for target in current:
            graph.add_edge(source, target, weight=.5)
tracemalloc.start()
report = exposure_report('root', graph, max_hops=5)
_, peak = tracemalloc.get_traced_memory()
tracemalloc.stop()
print(json.dumps(dict(python=platform.python_version(), platform=platform.platform(),
                      networkx=nx.__version__, nodes=len(graph), edges=graph.number_of_edges(),
                      branching=5, hops=5, peak_bytes=peak,
                      **{key: value for key, value in report.items() if key != 'exposures'}), indent=2))
