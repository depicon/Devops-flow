"""Registre applicatif explicite, sans découverte ou import du dépôt audité."""
from devops_flow.signals import ScannerRegistry


def default_registry():
    from devops_flow.signals.python import PythonScanner
    from devops_flow.signals.node import NodeScanner
    from devops_flow.signals.go import GoScanner
    from devops_flow.signals.java import JavaScanner
    from devops_flow.signals.rust import RustScanner
    return ScannerRegistry((PythonScanner(), NodeScanner(), GoScanner(), JavaScanner(), RustScanner()))
