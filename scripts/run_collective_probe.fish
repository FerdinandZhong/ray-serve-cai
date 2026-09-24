#!/usr/bin/env fish
# Run from a machine with kubectl access; no file copy into the pods needed.
if test (count $argv) -lt 2; or test (count $argv) -gt 3
    echo 'Usage: fish scripts/run_collective_probe.fish POD0 POD1 [NAMESPACE]' >&2
    exit 2
end

set pod0 $argv[1]
set pod1 $argv[2]
set ns mlx-user-2
if test (count $argv) -eq 3
    set ns $argv[3]
end
if test "$pod0" = "$pod1"
    echo 'Specify two different GPU worker pods.' >&2
    exit 2
end

set script_dir (dirname (status filename))
set probe "$script_dir/collective_probe.py"
if not test -r "$probe"
    echo "Missing probe file: $probe" >&2
    exit 2
end

set master_ip (kubectl -n "$ns" get pod "$pod0" -o jsonpath='{.status.podIP}')
if test $status -ne 0; or test -z "$master_ip"
    echo 'Cannot determine rank-0 pod IP.' >&2
    exit 1
end
kubectl -n "$ns" get pod "$pod1" -o name; or exit 1

set log_dir (mktemp -d ./collective-probe.XXXXXX); or exit 1
echo "Logs: $log_dir"
echo "Rendezvous: $master_ip:29601; process deadline: 180 seconds"

function run_probe --argument-names ns pod rank master probe
    kubectl -n "$ns" exec -i "$pod" -c engine -- \
        env NCCL_DEBUG=INFO NCCL_DEBUG_SUBSYS=INIT,NET,ENV \
        NCCL_IB_DISABLE=1 NCCL_SOCKET_IFNAME=eth0 \
        GLOO_SOCKET_IFNAME=eth0 TORCH_DISTRIBUTED_DEBUG=DETAIL \
        /home/cdsw/.venv-vllm/bin/python -u - \
        --rank "$rank" --master "$master" < "$probe"
end

# Background the external command directly so fish can wait for completion.
kubectl -n "$ns" exec -i "$pod0" -c engine -- \
    env NCCL_DEBUG=INFO NCCL_DEBUG_SUBSYS=INIT,NET,ENV \
    NCCL_IB_DISABLE=1 NCCL_SOCKET_IFNAME=eth0 \
    GLOO_SOCKET_IFNAME=eth0 TORCH_DISTRIBUTED_DEBUG=DETAIL \
    /home/cdsw/.venv-vllm/bin/python -u - \
    --rank 0 --master "$master_ip" < "$probe" > "$log_dir/rank0.log" 2>&1 &
set rank0_pid $last_pid

run_probe "$ns" "$pod1" 1 "$master_ip" "$probe" > "$log_dir/rank1.log" 2>&1
set rank1_status $status
wait $rank0_pid

cat "$log_dir/rank0.log" "$log_dir/rank1.log"
# fish wait does not report the child command's exit status; require both DONEs.
if test $rank1_status -ne 0
    exit 1
end
for rank in 0 1
    if not string match -q -- "RANK $rank: DONE" < "$log_dir/rank$rank.log"
        exit 1
    end
end
