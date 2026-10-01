#!/bin/sh
JQ_1OBJ='if length!=1 then error("docs") else .[0] end | if type!="object" then error("raiz") else . end'
JQ_TIPO="$JQ_1OBJ"' | .tipo | if type=="string" and test("\A[a-z][a-z-]*\z") then . else error("tipo") end'
JQ_JOBID="$JQ_1OBJ"' | .job_id | if type=="string" and test("\A[0-9a-f]{12}\z") then . else error("job_id") end'
chk(){ out=$(printf '%s' "$2" | jq -ers "$1" 2>/dev/null); echo "  rc=$? out=[$out]  <- $3"; }
echo "=== TIPO ==="
chk "$JQ_TIPO" '{"tipo":"purgar-papelera"}' "válido -> rc0 out=purgar-papelera"
chk "$JQ_TIPO" '{"tipo":"reconciliar"}' "válido -> rc0 out=reconciliar"
printf '{"tipo":"purgar-papelera\n"}' | { read -r LINE; :; }   # noop
chk "$JQ_TIPO" "$(printf '{"tipo":"purgar-papelera\n"}')" "salto FINAL -> rc!=0"
chk "$JQ_TIPO" "$(printf '{"tipo":"\nreconciliar"}')" "salto INICIAL -> rc!=0"
echo "=== JOBID ==="
chk "$JQ_JOBID" '{"job_id":"a1b2c3d4e5f6"}' "12hex -> rc0"
chk "$JQ_JOBID" "$(printf '{"job_id":"a1b2c3d4e5f6\n"}')" "12hex+salto -> rc!=0"
chk "$JQ_JOBID" '{"job_id":"a1b2c3d4e5f6a"}' "13hex -> rc!=0"
