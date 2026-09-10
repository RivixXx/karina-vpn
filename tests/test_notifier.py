import asyncio
from contextlib import closing
from copy import deepcopy
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
import pytest
from src import notifier
from src.models import ClientInfo, TrafficInfo

NOW=2_000_000_000; DAY=86_400_000; QUOTA=50*1024**3
def client(email="demo_target",days=5,expiry=None,enabled=True):
    value=NOW*1000+days*DAY if expiry is None else expiry
    return ClientInfo(email,"active",enabled,value,"",0,2,0,0,(1,),"","")
class Service:
    def __init__(self,clients,traffic=None,errors=()): self.clients=clients; self.traffic=traffic or {}; self.errors=set(errors)
    def list_clients(self): return deepcopy(self.clients)
    def get_mobile_traffic(self,email):
        if email in self.errors: raise RuntimeError("synthetic traffic failure")
        return self.traffic.get(email)
def run(service,db,**kw):
    sender=kw.pop("sender",AsyncMock())
    coroutine=notifier.run_notification_pass(service,sender,connect=db,now_provider=lambda:NOW,**kw)
    try:
        coroutine.send(None)
    except StopIteration as completed:
        result=completed.value
    else:
        coroutine.close()
        pytest.fail("unexpected asynchronous I/O")
    return result,sender
def sent(db,email="demo_target"):
    with closing(db()) as con:
        return con.execute("SELECT expiry_key,notification_day FROM notifications_sent WHERE email=? AND expiry_key!='fixture' ORDER BY id",(email,)).fetchall()

@pytest.mark.parametrize(("days","stage"),[(8,None),(5,7),(2,3),(10/24,1),(0,0),(-2,0)])
def test_expiry_windows_choose_only_current_milestone(local_db,days,stage):
    summary,sender=run(Service([client(days=days)]),local_db)
    if stage is None: sender.assert_not_awaited()
    else:
        sender.assert_awaited_once(); assert sender.await_args.args[2]==stage
        assert summary.expiry_sent==1 and len(sent(local_db))==1

def test_unlimited_and_legacy_primary_only(local_db):
    summary,sender=run(Service([client(expiry=0),client("demo_other",2)]),local_db)
    assert summary.expiry_sent==1 and sender.await_args.args[:3]==(2,"expiry",3)

def test_expiry_restart_dedup_and_renewal_cycle(local_db):
    service=Service([client(days=2)])
    assert run(service,local_db)[0].expiry_sent==1
    summary,sender=run(service,local_db); assert summary.duplicates==1; sender.assert_not_awaited()
    assert run(Service([client(days=2,expiry=NOW*1000+2*DAY+3_600_000)]),local_db)[0].expiry_sent==1
    assert len(sent(local_db))==2

@pytest.mark.parametrize(("ratio","stage"),[(.79,None),(.8,80),(.96,95),(1,100)])
def test_quota_selects_highest_threshold(local_db,ratio,stage):
    t=TrafficInfo(int(QUOTA*ratio),QUOTA,0,ratio*100)
    summary,sender=run(Service([client(days=20)],{"demo_target":t}),local_db)
    if stage is None: sender.assert_not_awaited()
    else: assert sender.await_args.args[1:3]==("quota",stage) and summary.quota_sent==1

def test_quota_progression_dedup_and_observed_reset(local_db):
    def scan(ratio):
        t=TrafficInfo(int(QUOTA*ratio),QUOTA,0,ratio*100)
        return run(Service([client(days=20)],{"demo_target":t}),local_db)[0]
    assert scan(.8).quota_sent==1; assert scan(.8).duplicates==1
    assert scan(.96).quota_sent==1; assert scan(1).quota_sent==1
    assert scan(.1).quota_sent==0; assert scan(.8).quota_sent==1
    assert [x[1] for x in sent(local_db)]==[80,95,100,80]

def test_mobile_expiry_ignored_and_exhaustion_does_not_disable_primary(local_db):
    primary=client(enabled=True); t=TrafficInfo(QUOTA,QUOTA,0,100)
    summary,_=run(Service([primary],{primary.email:t}),local_db)
    assert summary.expiry_sent==summary.quota_sent==1 and primary.enabled

def test_no_mobile_and_unbound_are_safe(local_db):
    summary,_=run(Service([client("demo_other"),client("unbound")]),local_db)
    assert summary.bound==1 and summary.unbound==1 and summary.quota_sent==0

def test_malformed_binding_is_unbound(local_db):
    with closing(local_db()) as con,con: con.execute("UPDATE telegram_links SET tg_id=0 WHERE email='demo_target'")
    summary,sender=run(Service([client()]),local_db)
    assert summary.unbound==1; sender.assert_not_awaited()

def test_failures_isolated_unrecorded_and_one_admin_summary(local_db):
    async def sender(tg_id,*args,**kwargs):
        if tg_id==1: raise RuntimeError("synthetic Telegram failure")
    admin=AsyncMock(); service=Service([client(),client("demo_other")],errors={"demo_other"})
    summary,_=run(service,local_db,sender=sender,admin_sender=admin,admin_tg_id=99)
    assert summary.users==2 and summary.errors==2 and sent(local_db)==[]
    admin.assert_awaited_once()

def test_clean_scan_has_no_admin_summary(local_db):
    admin=AsyncMock(); summary,_=run(Service([client(days=20)]),local_db,admin_sender=admin,admin_tg_id=99)
    assert summary.errors==0; admin.assert_not_awaited()

def test_dry_run_sends_and_writes_nothing(local_db):
    output=[]; t=TrafficInfo(int(QUOTA*.96),QUOTA,0,96); before=sent(local_db)
    summary,sender=run(Service([client()],{"demo_target":t}),local_db,dry_run=True,emit=output.append)
    sender.assert_not_awaited(); assert summary.expiry_sent==summary.quota_sent==0 and sent(local_db)==before
    with closing(local_db()) as con: assert con.execute("SELECT count(*) FROM notification_quota_cycles").fetchone()[0]==0
    assert output==["User: demo_target Expiry: expiry_7","User: demo_target Mobile: quota_95"]

def test_target_primary_and_reject_mobile(local_db):
    service=Service([client(),client("demo_other")]); assert run(service,local_db,target_user="demo_other")[0].users==1
    with pytest.raises(ValueError): run(service,local_db,target_user="demo__mobile")

def test_timezone_milliseconds_and_markdown():
    from datetime import datetime,timezone
    expiry=int(datetime(2026,9,16,21,30,tzinfo=timezone.utc).timestamp()*1000)
    text=notifier.notification_text(7,expiry,"Europe/Moscow")
    assert "17\\.09\\.2026" in text and "Карина VPN" in text

def test_history_preserved_and_cycle_identity(local_db):
    run(Service([client()]),local_db)
    with closing(local_db()) as con: keys=[x[0] for x in con.execute("SELECT expiry_key FROM notifications_sent WHERE email='demo_target'")]
    assert "fixture" in keys and any(x.startswith("expiry:") for x in keys)

def test_successful_send_creates_exactly_one_history_row(local_db):
    summary,sender=run(Service([client(days=2)]),local_db)
    sender.assert_awaited_once(); assert summary.expiry_sent==1 and len(sent(local_db))==1

def test_failed_send_records_nothing_and_retry_sends_again(local_db):
    attempts=[]
    async def fail(*args,**kwargs): attempts.append(args); raise RuntimeError("synthetic")
    first,_=run(Service([client(days=2)]),local_db,sender=fail)
    assert first.errors==1 and sent(local_db)==[] and len(attempts)==1
    second,sender=run(Service([client(days=2)]),local_db)
    assert second.expiry_sent==1 and len(sent(local_db))==1; sender.assert_awaited_once()

def test_history_write_failure_after_send_is_surfaced_and_retry_is_at_least_once(local_db):
    with closing(local_db()) as con,con:
        con.execute("CREATE TRIGGER fail_notification BEFORE INSERT ON notifications_sent BEGIN SELECT RAISE(ABORT,'synthetic'); END")
    sender=AsyncMock(); first,_=run(Service([client(days=2)]),local_db,sender=sender)
    assert first.errors==1 and sent(local_db)==[]; sender.assert_awaited_once()
    with closing(local_db()) as con,con: con.execute("DROP TRIGGER fail_notification")
    retry=AsyncMock(); second,_=run(Service([client(days=2)]),local_db,sender=retry)
    assert second.expiry_sent==1 and len(sent(local_db))==1; retry.assert_awaited_once()

def test_same_expiry_is_independently_scoped_by_logical_user(local_db):
    expiry=NOW*1000+5*DAY
    summary,sender=run(Service([client(expiry=expiry),client("demo_other",expiry=expiry)]),local_db)
    assert summary.expiry_sent==2 and sender.await_count==2
    assert len(sent(local_db,"demo_target"))==len(sent(local_db,"demo_other"))==1

def test_same_quota_cycle_is_independently_scoped_by_logical_user(local_db):
    traffic=TrafficInfo(int(QUOTA*.8),QUOTA,int(QUOTA*.2),80)
    summary,sender=run(Service([client(days=20),client("demo_other",days=20)],
                               {"demo_target":traffic,"demo_other":traffic}),local_db)
    assert summary.quota_sent==2 and sender.await_count==2
    assert len(sent(local_db,"demo_target"))==len(sent(local_db,"demo_other"))==1

def test_quota_reset_persists_across_restart_and_dry_run_does_not_advance(local_db):
    def scan(used,**kwargs):
        traffic=TrafficInfo(used,QUOTA,max(QUOTA-used,0),used/QUOTA*100)
        return run(Service([client(days=20)],{"demo_target":traffic}),local_db,**kwargs)[0]
    assert scan(45*1024**3).quota_sent==1
    output=[]; scan(2*1024**3,dry_run=True,emit=output.append)
    with closing(local_db()) as con:
        assert tuple(con.execute("SELECT cycle,last_used FROM notification_quota_cycles WHERE email='demo_target'").fetchone())==(1,45*1024**3)
    assert scan(2*1024**3).quota_sent==0
    with closing(local_db()) as con:
        assert tuple(con.execute("SELECT cycle,last_used FROM notification_quota_cycles WHERE email='demo_target'").fetchone())==(2,2*1024**3)
    assert scan(41*1024**3).quota_sent==1
    assert [row[0].split(":")[1] for row in sent(local_db)]==["1","2"]

def test_multiple_errors_and_failed_admin_summary_attempt_only_once(local_db):
    async def fail(*args,**kwargs): raise RuntimeError("synthetic")
    admin=AsyncMock(side_effect=RuntimeError("admin unavailable"))
    summary,_=run(Service([client(),client("demo_other")]),local_db,
                          sender=fail,admin_sender=admin,admin_tg_id=99)
    assert summary.errors==2 and admin.await_count==1

def test_contended_lock_has_no_side_effects(tmp_path,monkeypatch,local_db):
    class Fcntl:
        LOCK_EX=1; LOCK_NB=2
        @staticmethod
        def flock(*args): raise BlockingIOError
    import sys
    monkeypatch.setattr(notifier.os,"name","posix")
    monkeypatch.setitem(sys.modules,"fcntl",Fcntl)
    before=sent(local_db)
    with notifier.notifier_lock(tmp_path/"notifier.lock") as acquired: assert acquired is False
    assert sent(local_db)==before

def test_legacy_dedup_keys_remain_recognized(local_db):
    value=client(days=1)
    with closing(local_db()) as con,con:
        con.execute("INSERT INTO notifications_sent(email,expiry_key,notification_day,sent_at) VALUES(?,?,1,0)",
                    (value.email,str(value.expiry_time_ms)))
    sender=AsyncMock(); coroutine=notifier.process_expiring_client(
        NS(email=value.email,enabled=True,expiry_time_ms=value.expiry_time_ms,
           expiry_text="legacy",days_remaining=1),sender,connect=local_db)
    try: coroutine.send(None)
    except StopIteration as done: assert done.value=="already_sent"
    sender.assert_not_awaited()

def test_notifier_source_keeps_cli_and_network_orchestration_out():
    source=__import__("pathlib").Path("src/notifier.py").read_text(encoding="utf-8")
    assert "subprocess" not in source and "karina_user" not in source
