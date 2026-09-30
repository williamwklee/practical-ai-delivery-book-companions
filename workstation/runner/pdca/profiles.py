"""Normal consumer CLI sign-ins, no custom provider endpoint or key extraction."""
from pathlib import Path
import hashlib,json,os,shutil,subprocess,uuid

MODELS={'codex':'gpt-6-astra','claude':'claude-opus-5'}
def profile(provider):
    env=dict(os.environ)
    # Prevent environment API credentials/custom endpoints overriding the normal
    # consumer sign-in for this child. Saved login locations are unchanged.
    for key in ['OPENAI_API_KEY','CODEX_API_KEY','OPENAI_BASE_URL','ANTHROPIC_API_KEY',
                'ANTHROPIC_AUTH_TOKEN','ANTHROPIC_BASE_URL','CLAUDE_CODE_USE_BEDROCK',
                'CLAUDE_CODE_USE_VERTEX','CLAUDE_CODE_USE_FOUNDRY']:
        env.pop(key,None)
    if provider=='codex':
        argv=[shutil.which('codex'),'exec','--ephemeral','--sandbox','read-only','--json',
              '--strict-config','--ignore-user-config','--skip-git-repo-check','--model',MODELS[provider],
              '-c','model_reasoning_effort="high"','-c','approval_policy="never"','-c','web_search="disabled"']
        for feature in ['apps','plugins','hooks','multi_agent','shell_tool','unified_exec','browser_use',
                        'computer_use','image_generation','view_image','sleep_tool','skill_search',
                        'skill_mcp_dependency_install','goals','code_mode','code_mode_host','unbounded_connection_retries']:
            argv+=['--disable',feature]
    elif provider=='claude':
        argv=[shutil.which('claude'),'--print','--safe-mode','--strict-mcp-config','--mcp-config',
              '{"mcpServers":{}}','--tools','','--no-chrome','--no-session-persistence',
              '--model',MODELS[provider],'--effort','high','--max-turns','1',
              '--output-format','stream-json','--verbose']
        env.update(CLAUDE_CODE_MAX_RETRIES='0',CLAUDE_CODE_MAX_OUTPUT_TOKENS='4096')
    else:raise ValueError('unknown_cli')
    if argv[0] is None:raise ValueError('cli_not_installed')
    return argv,env

def login_status(provider,env):
    args=[provider,'login','status'] if provider=='codex' else [provider,'auth','status']
    r=subprocess.run(args,env=env,capture_output=True,text=True,timeout=15)
    if r.returncode:raise ValueError('consumer_sign_in_required')
    if provider=='codex':
        if 'Logged in using ChatGPT' not in r.stdout+r.stderr:raise ValueError('chatgpt_sign_in_required')
        return {'method':'ChatGPT sign-in','account_identifier':'not exposed by this status command'}
    d=json.loads(r.stdout)
    if d.get('loggedIn') is not True or d.get('apiProvider')!='firstParty' or d.get('authMethod') not in ['oauth_token','claude.ai']:
        raise ValueError('claude_consumer_sign_in_required')
    return {'method':d['authMethod'],'account_identifier':'not published; CLI manages login',
            'subscription_tier':d.get('subscriptionType'),'api_provider':d['apiProvider']}

def context(provider,argv,campaign):
    version=subprocess.run([provider,'--version'],capture_output=True,text=True,timeout=10)
    if version.returncode:raise ValueError('version_probe_failed')
    root=Path(__file__).resolve().parent
    source=b''.join(f.name.encode()+b'\0'+f.read_bytes()+b'\0' for f in sorted(root.glob('*.py')))
    return {'run_id':campaign+'/'+provider,'attempt_id':'attempt-'+uuid.uuid4().hex,
            'candidate_version':'C1 consumer','candidate_sha256':hashlib.sha256(source).hexdigest(),
            'candidate_hash_scope':'sorted top-level Python name/NUL/content/NUL',
            'authenticated_principal':'consumer CLI subscription sign-in; shared-tool identity tested separately',
            'cli_version':version.stdout.strip(),'effort':'high',
            'profile_sha256':hashlib.sha256(json.dumps(argv).encode()).hexdigest()}
