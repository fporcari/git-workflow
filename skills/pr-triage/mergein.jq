# Fold pr_mergestate.graphql's answer into the rows queue.jq produced.
# The merge state is a separate call because it is the most expensive field
# GitHub serves — see server/providers/github.py.
#
#   jq -s -f mergein.jq /tmp/q_merge.json /tmp/rows_core.json > /tmp/rows.json
(.[0].data.search.nodes | map(select(.))
 | map({key: (.number|tostring), value: .mergeStateStatus}) | from_entries) as $m
| .[1] | map(.merge = ($m[.n|tostring] // "UNKNOWN"))
