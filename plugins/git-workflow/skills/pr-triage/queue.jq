def spoke:
  [ (.comments.nodes[]?      | {t:.createdAt,  who:.author.login, ch:"comment"}),
    (.reviews.nodes[]?       | {t:.submittedAt,who:.author.login, ch:(.state|ascii_downcase)}),
    (.reviewThreads.nodes[]? | .comments.nodes[]? | {t:.createdAt,who:.author.login, ch:"inline"}) ]
  | map(select(.t))|sort_by(.t)|last;
[ inputs.data.search.nodes[] ]
| group_by(.number) | map(.[0]) | sort_by(.createdAt) | reverse
| map({ n:.number, title:.title, created:.createdAt[0:10], author:.author.login,
        draft:.isDraft, base:.baseRefName, merge:.mergeStateStatus,
        decision:.reviewDecision,
        req:[.reviewRequests.nodes[]?.requestedReviewer.login],
        reviews:[.reviews.nodes[]?|"\(.author.login):\(.state):\(.submittedAt[0:10])"],
        unresolved:([.reviewThreads.nodes[]?|select(.isResolved==false)]|length),
        threads:(.reviewThreads.nodes|length),
        closes:[.closingIssuesReferences.nodes[]?
                |{issue:.number,assignees:[.assignees.nodes[]?.login]}],
        last:(spoke // null) })
