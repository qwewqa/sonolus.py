# Resolves a circular import in some cases.
# While restructuring imports could be a better solution, there are some performance implications on hot paths.
from sonolus.script.internal import visitor as visitor
