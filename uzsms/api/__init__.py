"""The uzsms HTTP API.

Everything under this package depends on Django REST Framework, an
optional extra (``django-smsuz[drf]``). ``uzsms/urls.py`` is the guarded
entry point that host projects include; importing anything under this
package directly requires DRF to already be installed.
"""

from __future__ import annotations
