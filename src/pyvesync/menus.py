"""Framework-independent, explicitly prepared dual-chamber menu templates.

Serialization is owned by the caller: this module never reads or writes files,
uses browser storage, or contacts VeSync while editing a menu.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import TYPE_CHECKING, Any
from uuid import uuid4

if TYPE_CHECKING:
    from pyvesync.devices.vesynckitchen import VeSyncAirFryerDC111


def _text(value: object, field_name: str, *, required: bool = False) -> None:
    if not isinstance(value, str) or (required and not value.strip()):
        raise ValueError(f'{field_name} must be a nonempty string' if required else f'{field_name} must be a string')


@dataclass(frozen=True)
class ChamberProgram:
    """One chamber's settings; quantity is descriptive, never a scaling rule."""

    chamber: int
    temperature: int
    minutes: int
    mode: str = 'AirFry'
    dish: str = ''
    quantity: str = ''

    def __post_init__(self) -> None:
        if type(self.chamber) is not int or self.chamber not in (1, 2):
            raise ValueError('chamber must be 1 or 2')
        for name in ('temperature', 'minutes'):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(f'{name} must be a positive integer')
        _text(self.mode, 'mode', required=True)
        _text(self.dish, 'dish')
        _text(self.quantity, 'quantity')

    def to_dict(self) -> dict[str, Any]:
        """Return serializable, device-independent data."""
        return {'chamber': self.chamber, 'temperature': self.temperature,
                'minutes': self.minutes, 'mode': self.mode,
                'dish': self.dish, 'quantity': self.quantity}

    @classmethod
    def from_dict(cls, data: object) -> ChamberProgram:
        """Reject malformed or unknown data instead of silently dropping fields."""
        allowed = {'chamber', 'temperature', 'minutes', 'mode', 'dish', 'quantity'}
        required = {'chamber', 'temperature', 'minutes'}
        if not isinstance(data, dict) or set(data) - allowed or not required <= set(data):
            raise ValueError('invalid chamber program fields')
        return cls(**data)


@dataclass(frozen=True)
class FryerMenu:
    """A named menu containing one or two immutable chamber programs."""

    name: str
    chambers: tuple[ChamberProgram, ...]
    temperature_unit: str = 'celsius'
    menu_id: str = field(default_factory=lambda: uuid4().hex)

    def __post_init__(self) -> None:
        _text(self.name, 'name', required=True)
        _text(self.menu_id, 'menu_id', required=True)
        if self.temperature_unit not in ('celsius', 'fahrenheit'):
            raise ValueError('temperature_unit must be celsius or fahrenheit')
        if not isinstance(self.chambers, tuple) or not 1 <= len(self.chambers) <= 2:
            raise ValueError('chambers must be a tuple containing one or two programs')
        if any(not isinstance(p, ChamberProgram) for p in self.chambers):
            raise ValueError('every chamber program must be a ChamberProgram')
        if len({p.chamber for p in self.chambers}) != len(self.chambers):
            raise ValueError('duplicate chamber assignment')

    def to_dict(self) -> dict[str, Any]:
        """Return data without any device calls or storage side effects."""
        return {'menu_id': self.menu_id, 'name': self.name,
                'temperature_unit': self.temperature_unit,
                'chambers': [p.to_dict() for p in self.chambers]}

    @classmethod
    def from_dict(cls, data: object) -> FryerMenu:
        """Restore a menu and preserve its identifier."""
        keys = {'menu_id', 'name', 'temperature_unit', 'chambers'}
        if not isinstance(data, dict) or set(data) != keys or not isinstance(data['chambers'], list):
            raise ValueError('invalid menu fields')
        return cls(name=data['name'], menu_id=data['menu_id'],
                   temperature_unit=data['temperature_unit'],
                   chambers=tuple(ChamberProgram.from_dict(p) for p in data['chambers']))

    def programs_for(self, device: VeSyncAirFryerDC111) -> dict[int, dict[str, int | str]]:
        """Validate all programs against public device capabilities, without I/O.

        This does not invent supported modes or infer program-specific limits.
        The existing device methods remain responsible for final validation.
        """
        if self.temperature_unit != device.temp_unit:
            raise ValueError('menu and device temperature units differ')
        low, high = device.temperature_range
        programs: dict[int, dict[str, int | str]] = {}
        for p in self.chambers:
            if p.mode not in device.cook_modes:
                raise ValueError(f'unsupported cooking mode: {p.mode}')
            if not low <= p.temperature <= high:
                raise ValueError('temperature is outside the device range')
            programs[p.chamber] = {'temperature': p.temperature,
                                   'minutes': p.minutes, 'mode': p.mode}
        return programs


class MenuBook:
    """In-memory collection; the caller chooses where JSON is stored."""

    def __init__(self) -> None:
        self._menus: dict[str, FryerMenu] = {}

    def save(self, menu: FryerMenu, *, replace: bool = False) -> None:
        """Add a menu or explicitly replace an existing identifier."""
        if not isinstance(menu, FryerMenu):
            raise ValueError('menu must be a FryerMenu')
        if menu.menu_id in self._menus and not replace:
            raise ValueError('menu identifier already exists; use replace=True')
        if any(m.menu_id != menu.menu_id and m.name.strip().casefold() == menu.name.strip().casefold() for m in self._menus.values()):
            raise ValueError('menu name already exists')
        self._menus[menu.menu_id] = menu

    def get(self, menu_id: str) -> FryerMenu:
        """Return a menu; raise KeyError for a missing identifier."""
        return self._menus[menu_id]

    def remove(self, menu_id: str) -> FryerMenu:
        """Remove exactly one menu, without any appliance command."""
        return self._menus.pop(menu_id)

    def list_menus(self) -> tuple[FryerMenu, ...]:
        """Return an immutable snapshot of the collection."""
        return tuple(self._menus.values())

    def to_dict(self) -> dict[str, Any]:
        """Return a versioned interchange format."""
        return {'version': 1, 'menus': [m.to_dict() for m in self._menus.values()]}

    def to_json(self) -> str:
        """Serialize; writing files is deliberately left to the application."""
        return json.dumps(self.to_dict(), ensure_ascii=False, allow_nan=False)

    @classmethod
    def from_dict(cls, data: object) -> MenuBook:
        """Validate every entry before returning a reconstructed collection."""
        if (not isinstance(data, dict) or set(data) != {'version', 'menus'}
                or type(data['version']) is not int or data['version'] != 1
                or not isinstance(data['menus'], list)):
            raise ValueError('invalid or unsupported menu book format')
        book = cls()
        for value in data['menus']:
            book.save(FryerMenu.from_dict(value))
        return book

    @classmethod
    def from_json(cls, data: str) -> MenuBook:
        """Restore JSON without executing code or touching a device."""
        return cls.from_dict(json.loads(data))


async def prepare_menu(
    device: VeSyncAirFryerDC111,
    menu: FryerMenu,
    *,
    sync_type: int | None = None,
) -> bool:
    """Explicitly prepare an already validated menu; physical Start is required.

    For two chambers callers must explicitly choose sync_type=1 (finish together)
    or sync_type=2 (matching settings). No status refresh, login, retry, or start
    command is performed here. Callers should refresh device status beforehand.
    """
    programs = menu.programs_for(device)
    if len(programs) == 1:
        if sync_type is not None:
            raise ValueError('single-chamber menus do not take sync_type')
    elif type(sync_type) is not int or sync_type not in (1, 2):
        raise ValueError('two-chamber menus require explicit sync_type 1 or 2')
    elif sync_type == 2 and programs[1] != programs[2]:
        raise ValueError('matching mode requires identical program settings')
    for chamber in programs:
        state = device.state.chambers.get(chamber)
        if state is None or state.cook_status not in ('standby', 'ready', 'cooking_end', 'preheat_end'):
            raise ValueError(f'chamber {chamber} is active or unavailable')
    if len(programs) == 1:
        chamber = next(iter(programs))
        return await device.prepare_program(chamber=chamber, **programs[chamber])
    return await device.prepare_both_chambers(programs[1], programs[2], sync_type)
