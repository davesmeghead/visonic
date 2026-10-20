"""Exercise the bundled sequencer's download handlers without Home Assistant."""

import ast
import importlib
import importlib.util
import sys
from collections.abc import Callable
from datetime import datetime, timedelta
from enum import IntEnum
from pathlib import Path
from types import CodeType, FunctionType, SimpleNamespace
from typing import Literal, cast
from unittest.mock import Mock

import pytest

PACKAGE = (
    Path(__file__).resolve().parents[1] / "custom_components/visonic/direct/pyvisonic"
)
PACKAGE_NAME = "visonic_download_test_protocol"
spec = importlib.util.spec_from_file_location(
    PACKAGE_NAME, PACKAGE / "__init__.py", submodule_search_locations=[str(PACKAGE)]
)
assert spec is not None and spec.loader is not None
package = importlib.util.module_from_spec(spec)
sys.modules[PACKAGE_NAME] = package
spec.loader.exec_module(package)
protocol = importlib.import_module(f"{PACKAGE_NAME}.py_visonic_sequencer")
PanelErrorStates = protocol.PanelErrorStates
SequencerType = protocol.SequencerType
Handler = Literal["started", "doing"]
RunHandler = Callable[[Handler, IntEnum], IntEnum | None]


@pytest.fixture(scope="module")
def handlers() -> dict[str, CodeType]:
    """Compile the actual nested handlers, keeping their production bodies intact."""
    source = PACKAGE / "py_visonic_sequencer.py"
    tree = ast.parse(source.read_text(), filename=str(source))
    names = {"do_eeprom_started_download", "do_eeprom_doing_download"}
    return {
        node.name: next(
            code
            for code in compile(
                ast.Module(body=[node], type_ignores=[]), str(source), "exec"
            ).co_consts
            if isinstance(code, CodeType) and code.co_name == node.name
        )
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name in names
    }


@pytest.fixture
def panel() -> SimpleNamespace:
    now = protocol.get_utc_time()
    return SimpleNamespace(
        PowerMaster=False,
        pmExpectedResponse={0x02},
        pmDownloadComplete=False,
        pmDownloadInProgress=True,
        _last_send_download_eprom=now,
        _last_recv_time_panel_data=now,
    )


@pytest.fixture
def run_handler(panel: SimpleNamespace, handlers: dict[str, CodeType]) -> RunHandler:
    def run(handler: Handler, error: IntEnum) -> IntEnum | None:
        name = f"do_eeprom_{handler}_download"
        namespace = dict(vars(protocol))
        namespace.update(self=panel, processPanelErrorMessages=Mock(return_value=error))
        function = cast(
            Callable[..., IntEnum | None], FunctionType(handlers[name], namespace)
        )
        arguments = {"started": (panel._last_recv_time_panel_data,), "doing": ()}
        return function(*arguments[handler])

    return run


@pytest.mark.parametrize("handler", ["started", "doing"])
@pytest.mark.parametrize(
    ("power_master", "expected"),
    [
        pytest.param(False, None, id="powermax"),
        pytest.param(True, SequencerType.InitialisePanel, id="powermaster"),
        pytest.param(None, SequencerType.InitialisePanel, id="unidentified"),
    ],
)
def test_only_powermax_ignores_stop_denial(
    panel: SimpleNamespace,
    run_handler: RunHandler,
    handler: Handler,
    power_master: bool | None,
    expected: IntEnum | None,
) -> None:
    panel.PowerMaster = power_master
    assert run_handler(handler, PanelErrorStates.AccessDeniedStop) == expected
    assert panel.pmExpectedResponse == {0x02}


@pytest.mark.parametrize("handler", ["started", "doing"])
@pytest.mark.parametrize("power_master", [False, True, None])
def test_download_denial_still_retries(
    panel: SimpleNamespace,
    run_handler: RunHandler,
    handler: Handler,
    power_master: bool | None,
) -> None:
    panel.PowerMaster = power_master
    assert (
        run_handler(handler, PanelErrorStates.AccessDeniedDownload)
        == SequencerType.EPROMInitialiseDownload
    )
    assert panel.pmExpectedResponse == set()


@pytest.mark.parametrize(
    ("handler", "elapsed", "expected"),
    [
        pytest.param(
            "started",
            protocol.DOWNLOAD_RETRY_DELAY + 1,
            SequencerType.EPROMInitialiseDownload,
            id="start-timeout",
        ),
        pytest.param(
            "doing",
            protocol.DOWNLOAD_TIMEOUT + 1,
            SequencerType.InitialisePanel,
            id="download-timeout",
        ),
    ],
)
def test_ignored_stop_denial_preserves_timeouts(
    panel: SimpleNamespace,
    run_handler: RunHandler,
    handler: Handler,
    elapsed: int,
    expected: IntEnum,
) -> None:
    panel._last_send_download_eprom -= timedelta(seconds=elapsed)
    assert run_handler(handler, PanelErrorStates.AccessDeniedStop) == expected


def test_ignored_stop_denial_preserves_download_completion(
    panel: SimpleNamespace,
    run_handler: RunHandler,
) -> None:
    panel.pmDownloadComplete = True
    assert (
        run_handler("doing", PanelErrorStates.AccessDeniedStop)
        == SequencerType.EPROMDownloadComplete
    )


def test_ignored_stop_denial_preserves_download_progress(
    panel: SimpleNamespace,
    handlers: dict[str, CodeType],
) -> None:
    namespace = dict(vars(protocol))
    namespace.update(
        self=panel,
        processPanelErrorMessages=Mock(return_value=PanelErrorStates.AccessDeniedStop),
    )
    function = cast(
        Callable[[datetime], IntEnum | None],
        FunctionType(handlers["do_eeprom_started_download"], namespace),
    )
    assert (
        function(panel._last_recv_time_panel_data - timedelta(seconds=1))
        == SequencerType.EPROMDoingDownload
    )


@pytest.mark.parametrize("handler", ["started", "doing"])
@pytest.mark.parametrize(
    ("error", "expected"),
    [
        pytest.param(
            PanelErrorStates.DespatcherException, SequencerType.Reset, id="sender-error"
        ),
        pytest.param(
            PanelErrorStates.AccessDeniedPin,
            SequencerType.InitialisePanel,
            id="pin-denial",
        ),
    ],
)
def test_other_errors_keep_existing_handling(
    run_handler: RunHandler,
    handler: Handler,
    error: IntEnum,
    expected: IntEnum,
) -> None:
    assert run_handler(handler, error) == expected
