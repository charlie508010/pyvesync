"""Offline tests for menu storage and explicitly requested preparation."""
import asyncio
import copy
from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from pyvesync.menus import ChamberProgram, FryerMenu, MenuBook, prepare_menu


def menu(two=False):
    parts=(ChamberProgram(1,180,20,dish='Pommes',quantity='500 g'),)
    if two:parts+=(ChamberProgram(2,190,12,dish='Gemüse',quantity='2 Portionen'),)
    return FryerMenu('Abendessen',parts,menu_id='dinner')


def device():
    return SimpleNamespace(temp_unit='celsius',temperature_range=(80,205),cook_modes=['AirFry'],
        state=SimpleNamespace(chambers={n:SimpleNamespace(cook_status='standby') for n in (1,2)}),
        prepare_program=AsyncMock(return_value=True),prepare_both_chambers=AsyncMock(return_value=True))


def test_roundtrip_quantity_and_two_chambers():
    book=MenuBook();m=menu(True);book.save(m);restored=MenuBook.from_json(book.to_json())
    assert restored.get('dinner')==m
    assert restored.get('dinner').chambers[0].quantity=='500 g'
    assert restored.get('dinner').chambers[1].dish=='Gemüse'
    assert book.list_menus()==(m,)


def test_add_replace_remove_and_duplicates():
    b=MenuBook();m=menu();b.save(m)
    with pytest.raises(ValueError):b.save(m)
    with pytest.raises(ValueError):b.save(replace(m,menu_id='other',name=' ABENDESSEN '))
    updated=replace(m,name='Anderes Essen');b.save(updated,replace=True);assert b.get('dinner')==updated
    assert b.remove('dinner')==updated
    with pytest.raises(KeyError):b.remove('dinner')


def test_immutable_settings():
    with pytest.raises(FrozenInstanceError):menu().chambers[0].temperature=200


@pytest.mark.parametrize('field,value',[('chamber',True),('chamber',3),('temperature',True),('temperature',180.5),('minutes',0),('minutes',-1),('quantity',500),('mode','')])
def test_invalid_program(field,value):
    data=ChamberProgram(1,180,20).to_dict();data[field]=value
    with pytest.raises(ValueError):ChamberProgram.from_dict(data)


def test_schema_validation():
    data=menu().to_dict();data['chambers']*=2
    with pytest.raises(ValueError):FryerMenu.from_dict(data)
    data=menu().to_dict();data['extra']='ignored?'
    with pytest.raises(ValueError):FryerMenu.from_dict(data)
    b=MenuBook();b.save(menu());data=b.to_dict();data['menus']*=2
    with pytest.raises(ValueError):MenuBook.from_dict(data)
    for version in (True,2,'1'):
        with pytest.raises(ValueError):MenuBook.from_dict({'version':version,'menus':[]})


def test_conversion_has_no_commands():
    d=device();m=menu(True);programs=m.programs_for(d)
    assert programs[1]=={'temperature':180,'minutes':20,'mode':'AirFry'}
    assert programs[2]['temperature']==190
    d.prepare_program.assert_not_called();d.prepare_both_chambers.assert_not_called()


def test_explicit_single_chamber_two():
    d=device();m=FryerMenu('Nur Korb 2',(ChamberProgram(2,185,10),))
    assert asyncio.run(prepare_menu(d,m))
    d.prepare_program.assert_awaited_once_with(chamber=2,temperature=185,minutes=10,mode='AirFry')
    d.prepare_both_chambers.assert_not_called()


def test_two_chambers_explicit_sync_and_correct_mapping():
    d=device();m=menu(True)
    with pytest.raises(ValueError):asyncio.run(prepare_menu(d,m))
    d.prepare_both_chambers.assert_not_called()
    assert asyncio.run(prepare_menu(d,m,sync_type=1))
    args=d.prepare_both_chambers.await_args.args
    assert args[0]['temperature']==180 and args[1]['temperature']==190 and args[2]==1
    d.prepare_program.assert_not_called()


def test_match_must_really_match():
    d=device()
    with pytest.raises(ValueError):asyncio.run(prepare_menu(d,menu(True),sync_type=2))
    d.prepare_both_chambers.assert_not_called()
    m=FryerMenu('Gleich',(ChamberProgram(2,180,20),ChamberProgram(1,180,20)))
    assert asyncio.run(prepare_menu(d,m,sync_type=2))


@pytest.mark.parametrize('problem',['mode','unit','temperature','active','missing'])
def test_invalid_target_sends_nothing(problem):
    d=device();m=menu(True)
    if problem=='mode':d.cook_modes=[]
    if problem=='unit':d.temp_unit='fahrenheit'
    if problem=='temperature':d.temperature_range=(80,185)
    if problem=='active':d.state.chambers[2].cook_status='cooking'
    if problem=='missing':del d.state.chambers[2]
    with pytest.raises(ValueError):asyncio.run(prepare_menu(d,m,sync_type=1))
    d.prepare_program.assert_not_called();d.prepare_both_chambers.assert_not_called()


def test_device_failure_propagates_without_retry():
    d=device();d.prepare_program.return_value=False
    assert not asyncio.run(prepare_menu(d,menu()))
    d.prepare_program.assert_awaited_once()
    d=device();d.prepare_program.side_effect=RuntimeError('cloud unavailable')
    with pytest.raises(RuntimeError):asyncio.run(prepare_menu(d,menu()))
    d.prepare_program.assert_awaited_once()
