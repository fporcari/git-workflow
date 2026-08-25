# Decide who owes the next move on each of the user's PRs, and gate out every row
# that must not appear in a copy-pasteable chasing block.
#   jq --argjson owners '["a","b"]' --arg me a --arg today 2026-08-25 \
#      --argjson days 2 --arg trunk develop -f chase.jq /tmp/rows.json
def days_between($a;$b): (($a|strptime("%Y-%m-%d")|mktime) - ($b|strptime("%Y-%m-%d")|mktime))/86400;
# An approval is not a speech act: it does not hand the ball back to the author.
def owes_next: if (.last == null) or (.last.ch|test("approved")) then null else .last.who end;
[ .[] | select(.author==$me and .draft==false)
| { n, title, created, unresolved, base, merge, decision,
    age: days_between($today; .created),
    standing:(.req|unique),
    approved:  ([.reviews[]|select(test(":APPROVED:"))|split(":")[0]]|unique),
    dismissed: ([.reviews[]|select(test(":DISMISSED:"))|split(":")[0]]|unique),
    changes_by:([.reviews[]|select(test(":CHANGES_REQUESTED:"))|split(":")[0]]|unique),
    lastwho: owes_next }
| .eligible         = ($owners - [$me])
| .required_pending = (.standing - (.standing - .eligible))
# A live CHANGES_REQUESTED makes its author the target, not the codeowner:
# the next move is his re-review, and chasing anybody else is the wrong ask.
| .target = (if .decision == "CHANGES_REQUESTED" and (.changes_by|length) > 0
             then .changes_by[-1] else (.required_pending[0] // null) end)
| .gate =
    [ (if (.lastwho == null or .lastwho == $me) then empty else "ball:\(.lastwho)" end),
      (if .age > $days      then empty else "too-fresh(\(.age|floor)d)" end),
      (if .unresolved == 0  then empty else "unresolved:\(.unresolved)" end),
      (if .merge == "DIRTY" then "dirty" else empty end),
      (if .base == $trunk   then empty else "stacked-on:\(.base)" end),
      (if .target != null   then empty else "nobody-asked" end) ]
| .chaseable = ((.gate|length) == 0)
| .kind = (if (.chaseable|not) then "excluded"
           elif (.approved|length) > 0 then "ritardatario"
           else "mai-iniziata" end) ]
| { chase: (map(select(.chaseable))|group_by(.target)
            |map({who:.[0].target,
                  ritardatari:  (map(select(.kind=="ritardatario"))|map({n,age:(.age|floor),ok:.approved})),
                  mai_iniziate: (map(select(.kind=="mai-iniziata"))|map({n,age:(.age|floor)}))})),
    excluded: (map(select(.chaseable|not))|map({n,age:(.age|floor),why:.gate})),
    courtesy_dropped: (map(.standing - .eligible)|add|group_by(.)|map({who:.[0],count:length})) }
