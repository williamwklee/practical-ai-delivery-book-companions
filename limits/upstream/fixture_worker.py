"""Fault-injection worker. Every response is synthetic, never an AI invocation."""
import json, os, signal, subprocess, sys, time
case = sys.argv[1]
def emit(x):print(json.dumps(x), flush=True)
if case == 'codex_ok':
    emit({'type':'thread.started','thread_id':'fixture-thread'})
    emit({'type':'item.completed','item':{'type':'agent_message','text':'Fixture result: Oak fits the stated constraints.'}})
    emit({'type':'turn.completed','usage':{'input_tokens':100,'cached_input_tokens':20,'output_tokens':10}})
elif case == 'claude_ok':
    emit({'type':'assistant','message':{'model':'claude-opus-5','content':[{'type':'thinking','thinking':'PRIVATE_SYNTHETIC_THINKING'},{'type':'text','text':'First part.'}]}})
    emit({'type':'assistant','message':{'model':'claude-opus-5','content':[{'type':'text','text':'Continuation.'}]}})
    emit({'type':'result','subtype':'success','is_error':False,'result':'Continuation.','modelUsage':{'claude-opus-5':{'inputTokens':100,'outputTokens':10,'cacheReadInputTokens':0,'cacheCreationInputTokens':0}}})
elif case == 'failure':
    print('Authorization failed: FAKE_SECRET_MUST_STAY_PRIVATE',file=sys.stderr,flush=True)
    emit({'type':'error','message':'seeded authentication failure'})
    sys.exit(7)
elif case == 'truncated':
    emit({'type':'item.completed','item':{'type':'agent_message','text':'PASS'}})
elif case == 'malformed':
    print('{"type":',flush=True)
elif case == 'empty':
    pass
elif case == 'stdin_eof':
    text=sys.stdin.read()
    emit({'type':'item.completed','item':{'type':'agent_message','text':'EOF received: '+str(len(text))}})
    emit({'type':'turn.completed','usage':{'input_tokens':1,'cached_input_tokens':0,'output_tokens':1}})
elif case == 'stdin_wait':
    # A deliberately unreleased input stream: demonstrates no-output detection.
    # Actual adapter stdin is a finite regular file, tested separately above.
    read_fd, write_fd = os.pipe()
    os.read(read_fd, 1)
elif case == 'idle':
    emit({'type':'thread.started','thread_id':'fixture-idle'})
    time.sleep(30)
elif case == 'progress_forever':
    while True:
        emit({'type':'system','subtype':'heartbeat'})
        time.sleep(.04)
elif case == 'flood':
    while True:
        print('x'*8192,flush=True)
elif case == 'child_ignores_term':
    code='import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(30)'
    child=subprocess.Popen([sys.executable,'-c',code])
    emit({'type':'system','child_pid':child.pid})
    signal.signal(signal.SIGTERM,signal.SIG_IGN)
    time.sleep(30)
else:
    raise SystemExit('unknown fixture')
