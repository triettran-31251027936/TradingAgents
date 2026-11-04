"""Choosing the plan must replace per-role transports before the next run."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


def choose(mode):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is not installed")
    source = Path(__file__).resolve().parents[1] / "desktop/app.js"
    script = r"""
const fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(process.argv[1],'utf8'),mode=process.argv[2];
const seed={llm_provider:'codex_cli',quick_think_provider:'deepseek',deep_think_provider:'codex_cli',
 backend_url:'https://example.invalid/v1',quick_think_backend_url:'https://quick.invalid',
 deep_think_backend_url:'https://deep.invalid',quick_think_llm:'old-quick',deep_think_llm:'old-deep',
 temperature:0.7,max_tokens:4096,desktop_role_models:{'Market Analyst':{provider:'deepseek',model:'old'},
 'Portfolio Manager':{provider:'codex_cli',model:'old'},'Sentiment Analyst':{provider:'agy_cli',model:'old'}},
 checkpoint_enabled:true,desktop_budget:{enabled:true,deepseek_max_requests:18},
 desktop_vietnam:{enabled:true,paper_enabled:true}};
const requests=[],notices=[],elements={},state={config:structuredClone(seed)};
let refreshed=0,workflow=0;
const context={state,$:id=>elements[id]??=( {} ),
 api:async(action,payload={})=>{requests.push({action,payload});
  if(action==='subscription_status')return {signed_in:mode!=='signed_out'};
  if(action==='subscription_models')return {models:mode==='no_models'?[]:[{id:'plan-test-model'}]};
  if(action==='save_config'){if(mode==='save_failed')throw Error('Isolated save failure');return structuredClone(payload.config);}
  throw Error('Unexpected action '+action);
 },updateConfigUI:()=>{refreshed++;},renderWorkflow:()=>{workflow++;},notice:(...args)=>notices.push(args)};
vm.createContext(context);
vm.runInContext(source.slice(source.indexOf('async function refreshSubscription'),source.indexOf('function defaultModel')),context);
(async()=>{let error=null;try{await vm.runInContext('useSubscription()',context);}catch(e){error=e.message;}
 console.log(JSON.stringify({seed,config:state.config,requests,notices,refreshed,workflow,error}));
})().catch(e=>{console.error(e);process.exitCode=1;});
"""
    result = subprocess.run(
        [node, "-e", script, str(source), mode], text=True, capture_output=True, timeout=10
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_subscription_replaces_role_overrides_and_old_endpoints_without_inference():
    result = choose("success")
    cfg = result["config"]
    assert cfg["desktop_role_models"] == {}
    assert cfg["llm_provider"] == "chatgpt"
    assert cfg["quick_think_llm"] == cfg["deep_think_llm"] == "plan-test-model"
    for key in ("quick_think_provider", "deep_think_provider", "backend_url",
                "quick_think_backend_url", "deep_think_backend_url", "temperature", "max_tokens"):
        assert cfg[key] is None, key
    for key in ("desktop_vietnam", "desktop_budget", "checkpoint_enabled"):
        assert cfg[key] == result["seed"][key]
    assert [x["action"] for x in result["requests"]] == [
        "subscription_status", "subscription_models", "save_config"
    ]
    assert result["refreshed"] == result["workflow"] == 1
    assert result["notices"][-1][1] == "success"


@pytest.mark.parametrize("mode", ["signed_out", "no_models", "save_failed"])
def test_unsuccessful_selection_keeps_current_routing_and_never_reports_success(mode):
    result = choose(mode)
    assert result["config"] == result["seed"]
    assert result["refreshed"] == result["workflow"] == 0
    assert not any(len(x) > 1 and x[1] == "success" for x in result["notices"])
    if mode != "save_failed":
        assert not any(x["action"] == "save_config" for x in result["requests"])
