"""Tool manifest served by the MCP stub. GENERATED -- do not edit by hand.

Regenerate with `uv run python scripts/build_mcp_manifest.py` after changing the tools in
`mcp_server.py`; `test_manifest_is_up_to_date` fails if this drifts.

Kept as a Python module rather than a data file so the stub can import it with no file IO
and no PyInstaller `datas` entry, working identically frozen and from source.
"""

MANIFEST = {'instructions': 'PhotonFinder manages an astrophotography file library (FITS/XISF images and '
                 'calibration frames). Use `search_files` with a SearchCriteria JSON object to '
                 'find files; use `list_library_roots`, `list_projects` and `list_distinct_values` '
                 'to discover valid filter values. Start with `list_library_roots`: each root '
                 'carries a user-written description of what it contains, which is the quickest '
                 'way to orient yourself in an unfamiliar library. Use `get_project_details` to '
                 "inspect a single project, `get_file_details` to inspect one file's full metadata "
                 "and FITS header, and `lookup_object`/`list_catalogs` to resolve an object's "
                 "RA/Dec from PhotonFinder's local catalog database (no online lookups such as "
                 'Simbad or Telescopius are performed). `report_targets` and '
                 "`report_catalog_coverage` give the same aggregates as the application's Report "
                 'menu -- total integration per target (matched by object name; no plate solving '
                 'required), and which catalog objects the library covers (only considering '
                 'plate-solved images) -- and `get_header_values` reads a few named FITS/model/WCS '
                 'fields across many files at once instead of one whole header at a time. Every '
                 'one of these is read-only, and PhotonFinder never writes files on your behalf: '
                 'report content is returned to you to save as you see fit. `plate_solve_files` is '
                 'the sole exception and the user must opt into it in Settings; it plate-solves up '
                 'to 10 files at once and writes the resulting WCS/coordinates to the library.',
 'tools': [{'name': 'search_files',
            'description': 'Search the file library.\n'
                           '\n'
                           'Returns `{results, page, page_size, total, has_more}`. `page` is '
                           'zero-based.\n'
                           'Discover valid values for `filter`/`type`/`camera`/etc. via '
                           '`list_distinct_values`,\n'
                           'and valid `paths` roots via `list_library_roots`.\n',
            'inputSchema': {'$defs': {'SearchCriteriaInput': {'description': 'Search filters. Omit '
                                                                             'any field to leave '
                                                                             'it unconstrained.',
                                                              'properties': {'type': {'anyOf': [{'type': 'string'},
                                                                                                {'type': 'null'}],
                                                                                      'default': None,
                                                                                      'description': 'LIGHT/DARK/FLAT/BIAS/MASTER '
                                                                                                     '...',
                                                                                      'title': 'Type'},
                                                                             'filter': {'anyOf': [{'type': 'string'},
                                                                                                  {'type': 'null'}],
                                                                                        'default': None,
                                                                                        'title': 'Filter'},
                                                                             'camera': {'anyOf': [{'type': 'string'},
                                                                                                  {'type': 'null'}],
                                                                                        'default': None,
                                                                                        'title': 'Camera'},
                                                                             'telescope': {'anyOf': [{'type': 'string'},
                                                                                                     {'type': 'null'}],
                                                                                           'default': None,
                                                                                           'title': 'Telescope'},
                                                                             'object_name': {'anyOf': [{'type': 'string'},
                                                                                                       {'type': 'null'}],
                                                                                             'default': None,
                                                                                             'title': 'Object '
                                                                                                      'Name'},
                                                                             'file_name': {'anyOf': [{'type': 'string'},
                                                                                                     {'type': 'null'}],
                                                                                           'default': None,
                                                                                           'title': 'File '
                                                                                                    'Name'},
                                                                             'exposure': {'anyOf': [{'type': 'string'},
                                                                                                    {'type': 'null'}],
                                                                                          'default': None,
                                                                                          'description': 'Exposure '
                                                                                                         'time '
                                                                                                         'in '
                                                                                                         'seconds.',
                                                                                          'title': 'Exposure'},
                                                                             'exposure_tolerance': {'anyOf': [{'type': 'number'},
                                                                                                              {'type': 'null'}],
                                                                                                    'default': None,
                                                                                                    'description': '± '
                                                                                                                   'seconds '
                                                                                                                   'tolerance '
                                                                                                                   'for '
                                                                                                                   '`exposure`; '
                                                                                                                   'omit '
                                                                                                                   'for '
                                                                                                                   'an '
                                                                                                                   'exact '
                                                                                                                   'match.',
                                                                                                    'title': 'Exposure '
                                                                                                             'Tolerance'},
                                                                             'binning': {'anyOf': [{'type': 'string'},
                                                                                                   {'type': 'null'}],
                                                                                         'default': None,
                                                                                         'title': 'Binning'},
                                                                             'gain': {'anyOf': [{'type': 'string'},
                                                                                                {'type': 'null'}],
                                                                                      'default': None,
                                                                                      'title': 'Gain'},
                                                                             'offset': {'anyOf': [{'type': 'integer'},
                                                                                                  {'type': 'null'}],
                                                                                        'default': None,
                                                                                        'title': 'Offset'},
                                                                             'temperature': {'anyOf': [{'type': 'string'},
                                                                                                       {'type': 'null'}],
                                                                                             'default': None,
                                                                                             'title': 'Temperature'},
                                                                             'temperature_tolerance': {'anyOf': [{'type': 'number'},
                                                                                                                 {'type': 'null'}],
                                                                                                       'default': None,
                                                                                                       'description': '± '
                                                                                                                      '°C '
                                                                                                                      'tolerance '
                                                                                                                      'for '
                                                                                                                      '`temperature`; '
                                                                                                                      'omit '
                                                                                                                      'for '
                                                                                                                      'an '
                                                                                                                      'exact '
                                                                                                                      'match.',
                                                                                                       'title': 'Temperature '
                                                                                                                'Tolerance'},
                                                                             'coord_ra': {'anyOf': [{'type': 'string'},
                                                                                                    {'type': 'null'}],
                                                                                          'default': None,
                                                                                          'description': 'Right '
                                                                                                         'Ascension '
                                                                                                         'in '
                                                                                                         'hours.',
                                                                                          'title': 'Coord '
                                                                                                   'Ra'},
                                                                             'coord_dec': {'anyOf': [{'type': 'string'},
                                                                                                     {'type': 'null'}],
                                                                                           'default': None,
                                                                                           'description': 'Declination '
                                                                                                          'in '
                                                                                                          'degrees.',
                                                                                           'title': 'Coord '
                                                                                                    'Dec'},
                                                                             'coord_radius': {'anyOf': [{'type': 'number'},
                                                                                                        {'type': 'null'}],
                                                                                              'default': None,
                                                                                              'description': 'Cone '
                                                                                                             'search '
                                                                                                             'radius '
                                                                                                             'in '
                                                                                                             'decimal '
                                                                                                             'degrees '
                                                                                                             '(used '
                                                                                                             'with '
                                                                                                             '`coord_ra`/`coord_dec`).',
                                                                                              'title': 'Coord '
                                                                                                       'Radius'},
                                                                             'start_datetime': {'anyOf': [{'format': 'date-time',
                                                                                                           'type': 'string'},
                                                                                                          {'type': 'null'}],
                                                                                                'default': None,
                                                                                                'title': 'Start '
                                                                                                         'Datetime'},
                                                                             'end_datetime': {'anyOf': [{'format': 'date-time',
                                                                                                         'type': 'string'},
                                                                                                        {'type': 'null'}],
                                                                                              'default': None,
                                                                                              'title': 'End '
                                                                                                       'Datetime'},
                                                                             'header_text': {'anyOf': [{'type': 'string'},
                                                                                                       {'type': 'null'}],
                                                                                             'default': None,
                                                                                             'description': 'Free '
                                                                                                            'text '
                                                                                                            'or '
                                                                                                            'a '
                                                                                                            '"KEYWORD=value"/"KEYWORD<value" '
                                                                                                            'style '
                                                                                                            'FITS '
                                                                                                            'header '
                                                                                                            'match, '
                                                                                                            'e.g. '
                                                                                                            '"GAIN=100", '
                                                                                                            '"FOCTEMP<0".',
                                                                                             'title': 'Header '
                                                                                                      'Text'},
                                                                             'plate_solved': {'anyOf': [{'type': 'boolean'},
                                                                                                        {'type': 'null'}],
                                                                                              'default': None,
                                                                                              'description': 'True '
                                                                                                             '= '
                                                                                                             'solved '
                                                                                                             'only, '
                                                                                                             'False '
                                                                                                             '= '
                                                                                                             'unsolved '
                                                                                                             'only, '
                                                                                                             'omit '
                                                                                                             '= '
                                                                                                             'either.',
                                                                                              'title': 'Plate '
                                                                                                       'Solved'},
                                                                             'project': {'anyOf': [{'type': 'integer'},
                                                                                                   {'type': 'null'}],
                                                                                         'default': None,
                                                                                         'description': 'A '
                                                                                                        'project '
                                                                                                        'rowid '
                                                                                                        'from '
                                                                                                        '`list_projects`, '
                                                                                                        'or '
                                                                                                        '-1 '
                                                                                                        'to '
                                                                                                        'match '
                                                                                                        'files '
                                                                                                        'not '
                                                                                                        'assigned '
                                                                                                        'to '
                                                                                                        'any '
                                                                                                        'project.',
                                                                                         'title': 'Project'},
                                                                             'paths': {'anyOf': [{'items': {'$ref': '#/$defs/_RootAndPathInput'},
                                                                                                  'type': 'array'},
                                                                                                 {'type': 'null'}],
                                                                                       'default': None,
                                                                                       'description': 'Restrict '
                                                                                                      'the '
                                                                                                      'search '
                                                                                                      'to '
                                                                                                      'one '
                                                                                                      'or '
                                                                                                      'more '
                                                                                                      'library '
                                                                                                      'roots.',
                                                                                       'title': 'Paths'},
                                                                             'width_min': {'anyOf': [{'type': 'integer'},
                                                                                                     {'type': 'null'}],
                                                                                           'default': None,
                                                                                           'title': 'Width '
                                                                                                    'Min'},
                                                                             'width_max': {'anyOf': [{'type': 'integer'},
                                                                                                     {'type': 'null'}],
                                                                                           'default': None,
                                                                                           'title': 'Width '
                                                                                                    'Max'},
                                                                             'height_min': {'anyOf': [{'type': 'integer'},
                                                                                                      {'type': 'null'}],
                                                                                            'default': None,
                                                                                            'title': 'Height '
                                                                                                     'Min'},
                                                                             'height_max': {'anyOf': [{'type': 'integer'},
                                                                                                      {'type': 'null'}],
                                                                                            'default': None,
                                                                                            'title': 'Height '
                                                                                                     'Max'},
                                                                             'scale_min': {'anyOf': [{'type': 'number'},
                                                                                                     {'type': 'null'}],
                                                                                           'default': None,
                                                                                           'description': 'Plate '
                                                                                                          'scale, '
                                                                                                          'arcsec/pixel.',
                                                                                           'title': 'Scale '
                                                                                                    'Min'},
                                                                             'scale_max': {'anyOf': [{'type': 'number'},
                                                                                                     {'type': 'null'}],
                                                                                           'default': None,
                                                                                           'title': 'Scale '
                                                                                                    'Max'},
                                                                             'star_count_min': {'anyOf': [{'type': 'integer'},
                                                                                                          {'type': 'null'}],
                                                                                                'default': None,
                                                                                                'title': 'Star '
                                                                                                         'Count '
                                                                                                         'Min'},
                                                                             'star_count_max': {'anyOf': [{'type': 'integer'},
                                                                                                          {'type': 'null'}],
                                                                                                'default': None,
                                                                                                'title': 'Star '
                                                                                                         'Count '
                                                                                                         'Max'},
                                                                             'fwhm_min': {'anyOf': [{'type': 'number'},
                                                                                                    {'type': 'null'}],
                                                                                          'default': None,
                                                                                          'title': 'Fwhm '
                                                                                                   'Min'},
                                                                             'fwhm_max': {'anyOf': [{'type': 'number'},
                                                                                                    {'type': 'null'}],
                                                                                          'default': None,
                                                                                          'title': 'Fwhm '
                                                                                                   'Max'},
                                                                             'background_min': {'anyOf': [{'type': 'number'},
                                                                                                          {'type': 'null'}],
                                                                                                'default': None,
                                                                                                'title': 'Background '
                                                                                                         'Min'},
                                                                             'background_max': {'anyOf': [{'type': 'number'},
                                                                                                          {'type': 'null'}],
                                                                                                'default': None,
                                                                                                'title': 'Background '
                                                                                                         'Max'},
                                                                             'background_rms_min': {'anyOf': [{'type': 'number'},
                                                                                                              {'type': 'null'}],
                                                                                                    'default': None,
                                                                                                    'title': 'Background '
                                                                                                             'Rms '
                                                                                                             'Min'},
                                                                             'background_rms_max': {'anyOf': [{'type': 'number'},
                                                                                                              {'type': 'null'}],
                                                                                                    'default': None,
                                                                                                    'title': 'Background '
                                                                                                             'Rms '
                                                                                                             'Max'},
                                                                             'elongation_min': {'anyOf': [{'type': 'number'},
                                                                                                          {'type': 'null'}],
                                                                                                'default': None,
                                                                                                'title': 'Elongation '
                                                                                                         'Min'},
                                                                             'elongation_max': {'anyOf': [{'type': 'number'},
                                                                                                          {'type': 'null'}],
                                                                                                'default': None,
                                                                                                'title': 'Elongation '
                                                                                                         'Max'},
                                                                             'sorting_field': {'anyOf': [{'enum': ['name',
                                                                                                                   'path',
                                                                                                                   'size',
                                                                                                                   'mtime',
                                                                                                                   'type',
                                                                                                                   'filter',
                                                                                                                   'exposure',
                                                                                                                   'gain',
                                                                                                                   'offset',
                                                                                                                   'binning',
                                                                                                                   'temperature',
                                                                                                                   'camera',
                                                                                                                   'telescope',
                                                                                                                   'object_name',
                                                                                                                   'date_obs',
                                                                                                                   'coord_ra',
                                                                                                                   'coord_dec',
                                                                                                                   'star_count',
                                                                                                                   'fwhm',
                                                                                                                   'elongation',
                                                                                                                   'background',
                                                                                                                   'background_rms'],
                                                                                                          'type': 'string'},
                                                                                                         {'type': 'null'}],
                                                                                               'default': None,
                                                                                               'description': 'Field '
                                                                                                              'to '
                                                                                                              'sort '
                                                                                                              'results '
                                                                                                              'by. '
                                                                                                              'Omit '
                                                                                                              'for '
                                                                                                              'the '
                                                                                                              'default '
                                                                                                              'root/path/name '
                                                                                                              'order.',
                                                                                               'title': 'Sorting '
                                                                                                        'Field'},
                                                                             'sorting_desc': {'anyOf': [{'type': 'boolean'},
                                                                                                        {'type': 'null'}],
                                                                                              'default': None,
                                                                                              'description': 'Sort '
                                                                                                             'direction '
                                                                                                             'for '
                                                                                                             '`sorting_field`; '
                                                                                                             'defaults '
                                                                                                             'to '
                                                                                                             'True '
                                                                                                             '(descending) '
                                                                                                             'if '
                                                                                                             'omitted.',
                                                                                              'title': 'Sorting '
                                                                                                       'Desc'}},
                                                              'title': 'SearchCriteriaInput',
                                                              'type': 'object'},
                                      '_RootAndPathInput': {'properties': {'root_id': {'description': 'A '
                                                                                                      'library '
                                                                                                      "root's "
                                                                                                      'rowid, '
                                                                                                      'from '
                                                                                                      '`list_library_roots`.',
                                                                                       'title': 'Root '
                                                                                                'Id',
                                                                                       'type': 'integer'},
                                                                           'path': {'anyOf': [{'type': 'string'},
                                                                                              {'type': 'null'}],
                                                                                    'default': None,
                                                                                    'description': 'Subdirectory '
                                                                                                   'prefix '
                                                                                                   'within '
                                                                                                   'the '
                                                                                                   'root '
                                                                                                   'to '
                                                                                                   'narrow '
                                                                                                   'the '
                                                                                                   'search. '
                                                                                                   'Omit '
                                                                                                   'to '
                                                                                                   'match '
                                                                                                   'the '
                                                                                                   'whole '
                                                                                                   'root.',
                                                                                    'title': 'Path'},
                                                                           'root_label': {'anyOf': [{'type': 'string'},
                                                                                                    {'type': 'null'}],
                                                                                          'default': None,
                                                                                          'description': 'Display-only; '
                                                                                                         'accepted '
                                                                                                         'for '
                                                                                                         'symmetry '
                                                                                                         'with '
                                                                                                         '`list_library_roots` '
                                                                                                         'output '
                                                                                                         'but '
                                                                                                         'not '
                                                                                                         'used '
                                                                                                         'for '
                                                                                                         'filtering. '
                                                                                                         'May '
                                                                                                         'be '
                                                                                                         'omitted.',
                                                                                          'title': 'Root '
                                                                                                   'Label'}},
                                                            'required': ['root_id'],
                                                            'title': '_RootAndPathInput',
                                                            'type': 'object'}},
                            'properties': {'criteria': {'anyOf': [{'$ref': '#/$defs/SearchCriteriaInput'},
                                                                  {'type': 'null'}],
                                                        'default': None},
                                           'page': {'default': 0,
                                                    'title': 'Page',
                                                    'type': 'integer'},
                                           'page_size': {'default': 100,
                                                         'title': 'Page Size',
                                                         'type': 'integer'}},
                            'title': 'search_filesArguments',
                            'type': 'object'},
            'annotations': {'title': 'Search files', 'readOnlyHint': True, 'openWorldHint': False}},
           {'name': 'report_targets',
            'description': 'Total integration time per target, grouped by object / filter / '
                           'telescope / camera.\n'
                           '\n'
                           "The same aggregate as the application's Target Report, over the same "
                           '`criteria` as\n'
                           '`search_files`. Each row carries the summed exposure in seconds, the '
                           'file count, the\n'
                           'most recent observation, and the library directories the files sit in. '
                           'Files with no\n'
                           'object name are excluded. Grouping is purely by the `object_name` '
                           'recorded in each\n'
                           "file's header (plus filter/telescope/camera) -- plate solving is not "
                           'required.\n'
                           '\n'
                           'Returns `{results, page, page_size, total, has_more, '
                           'total_exposure_seconds_all}`,\n'
                           'where `total_exposure_seconds_all` sums every group, not just this '
                           'page. Set\n'
                           '`include_paths=false` when you only need integration totals; `paths` '
                           'is capped at\n'
                           '`max_paths` per row and `paths_truncated` says when it hit the cap. '
                           'Narrow `criteria`\n'
                           'rather than paging through thousands of groups.\n',
            'inputSchema': {'$defs': {'SearchCriteriaInput': {'description': 'Search filters. Omit '
                                                                             'any field to leave '
                                                                             'it unconstrained.',
                                                              'properties': {'type': {'anyOf': [{'type': 'string'},
                                                                                                {'type': 'null'}],
                                                                                      'default': None,
                                                                                      'description': 'LIGHT/DARK/FLAT/BIAS/MASTER '
                                                                                                     '...',
                                                                                      'title': 'Type'},
                                                                             'filter': {'anyOf': [{'type': 'string'},
                                                                                                  {'type': 'null'}],
                                                                                        'default': None,
                                                                                        'title': 'Filter'},
                                                                             'camera': {'anyOf': [{'type': 'string'},
                                                                                                  {'type': 'null'}],
                                                                                        'default': None,
                                                                                        'title': 'Camera'},
                                                                             'telescope': {'anyOf': [{'type': 'string'},
                                                                                                     {'type': 'null'}],
                                                                                           'default': None,
                                                                                           'title': 'Telescope'},
                                                                             'object_name': {'anyOf': [{'type': 'string'},
                                                                                                       {'type': 'null'}],
                                                                                             'default': None,
                                                                                             'title': 'Object '
                                                                                                      'Name'},
                                                                             'file_name': {'anyOf': [{'type': 'string'},
                                                                                                     {'type': 'null'}],
                                                                                           'default': None,
                                                                                           'title': 'File '
                                                                                                    'Name'},
                                                                             'exposure': {'anyOf': [{'type': 'string'},
                                                                                                    {'type': 'null'}],
                                                                                          'default': None,
                                                                                          'description': 'Exposure '
                                                                                                         'time '
                                                                                                         'in '
                                                                                                         'seconds.',
                                                                                          'title': 'Exposure'},
                                                                             'exposure_tolerance': {'anyOf': [{'type': 'number'},
                                                                                                              {'type': 'null'}],
                                                                                                    'default': None,
                                                                                                    'description': '± '
                                                                                                                   'seconds '
                                                                                                                   'tolerance '
                                                                                                                   'for '
                                                                                                                   '`exposure`; '
                                                                                                                   'omit '
                                                                                                                   'for '
                                                                                                                   'an '
                                                                                                                   'exact '
                                                                                                                   'match.',
                                                                                                    'title': 'Exposure '
                                                                                                             'Tolerance'},
                                                                             'binning': {'anyOf': [{'type': 'string'},
                                                                                                   {'type': 'null'}],
                                                                                         'default': None,
                                                                                         'title': 'Binning'},
                                                                             'gain': {'anyOf': [{'type': 'string'},
                                                                                                {'type': 'null'}],
                                                                                      'default': None,
                                                                                      'title': 'Gain'},
                                                                             'offset': {'anyOf': [{'type': 'integer'},
                                                                                                  {'type': 'null'}],
                                                                                        'default': None,
                                                                                        'title': 'Offset'},
                                                                             'temperature': {'anyOf': [{'type': 'string'},
                                                                                                       {'type': 'null'}],
                                                                                             'default': None,
                                                                                             'title': 'Temperature'},
                                                                             'temperature_tolerance': {'anyOf': [{'type': 'number'},
                                                                                                                 {'type': 'null'}],
                                                                                                       'default': None,
                                                                                                       'description': '± '
                                                                                                                      '°C '
                                                                                                                      'tolerance '
                                                                                                                      'for '
                                                                                                                      '`temperature`; '
                                                                                                                      'omit '
                                                                                                                      'for '
                                                                                                                      'an '
                                                                                                                      'exact '
                                                                                                                      'match.',
                                                                                                       'title': 'Temperature '
                                                                                                                'Tolerance'},
                                                                             'coord_ra': {'anyOf': [{'type': 'string'},
                                                                                                    {'type': 'null'}],
                                                                                          'default': None,
                                                                                          'description': 'Right '
                                                                                                         'Ascension '
                                                                                                         'in '
                                                                                                         'hours.',
                                                                                          'title': 'Coord '
                                                                                                   'Ra'},
                                                                             'coord_dec': {'anyOf': [{'type': 'string'},
                                                                                                     {'type': 'null'}],
                                                                                           'default': None,
                                                                                           'description': 'Declination '
                                                                                                          'in '
                                                                                                          'degrees.',
                                                                                           'title': 'Coord '
                                                                                                    'Dec'},
                                                                             'coord_radius': {'anyOf': [{'type': 'number'},
                                                                                                        {'type': 'null'}],
                                                                                              'default': None,
                                                                                              'description': 'Cone '
                                                                                                             'search '
                                                                                                             'radius '
                                                                                                             'in '
                                                                                                             'decimal '
                                                                                                             'degrees '
                                                                                                             '(used '
                                                                                                             'with '
                                                                                                             '`coord_ra`/`coord_dec`).',
                                                                                              'title': 'Coord '
                                                                                                       'Radius'},
                                                                             'start_datetime': {'anyOf': [{'format': 'date-time',
                                                                                                           'type': 'string'},
                                                                                                          {'type': 'null'}],
                                                                                                'default': None,
                                                                                                'title': 'Start '
                                                                                                         'Datetime'},
                                                                             'end_datetime': {'anyOf': [{'format': 'date-time',
                                                                                                         'type': 'string'},
                                                                                                        {'type': 'null'}],
                                                                                              'default': None,
                                                                                              'title': 'End '
                                                                                                       'Datetime'},
                                                                             'header_text': {'anyOf': [{'type': 'string'},
                                                                                                       {'type': 'null'}],
                                                                                             'default': None,
                                                                                             'description': 'Free '
                                                                                                            'text '
                                                                                                            'or '
                                                                                                            'a '
                                                                                                            '"KEYWORD=value"/"KEYWORD<value" '
                                                                                                            'style '
                                                                                                            'FITS '
                                                                                                            'header '
                                                                                                            'match, '
                                                                                                            'e.g. '
                                                                                                            '"GAIN=100", '
                                                                                                            '"FOCTEMP<0".',
                                                                                             'title': 'Header '
                                                                                                      'Text'},
                                                                             'plate_solved': {'anyOf': [{'type': 'boolean'},
                                                                                                        {'type': 'null'}],
                                                                                              'default': None,
                                                                                              'description': 'True '
                                                                                                             '= '
                                                                                                             'solved '
                                                                                                             'only, '
                                                                                                             'False '
                                                                                                             '= '
                                                                                                             'unsolved '
                                                                                                             'only, '
                                                                                                             'omit '
                                                                                                             '= '
                                                                                                             'either.',
                                                                                              'title': 'Plate '
                                                                                                       'Solved'},
                                                                             'project': {'anyOf': [{'type': 'integer'},
                                                                                                   {'type': 'null'}],
                                                                                         'default': None,
                                                                                         'description': 'A '
                                                                                                        'project '
                                                                                                        'rowid '
                                                                                                        'from '
                                                                                                        '`list_projects`, '
                                                                                                        'or '
                                                                                                        '-1 '
                                                                                                        'to '
                                                                                                        'match '
                                                                                                        'files '
                                                                                                        'not '
                                                                                                        'assigned '
                                                                                                        'to '
                                                                                                        'any '
                                                                                                        'project.',
                                                                                         'title': 'Project'},
                                                                             'paths': {'anyOf': [{'items': {'$ref': '#/$defs/_RootAndPathInput'},
                                                                                                  'type': 'array'},
                                                                                                 {'type': 'null'}],
                                                                                       'default': None,
                                                                                       'description': 'Restrict '
                                                                                                      'the '
                                                                                                      'search '
                                                                                                      'to '
                                                                                                      'one '
                                                                                                      'or '
                                                                                                      'more '
                                                                                                      'library '
                                                                                                      'roots.',
                                                                                       'title': 'Paths'},
                                                                             'width_min': {'anyOf': [{'type': 'integer'},
                                                                                                     {'type': 'null'}],
                                                                                           'default': None,
                                                                                           'title': 'Width '
                                                                                                    'Min'},
                                                                             'width_max': {'anyOf': [{'type': 'integer'},
                                                                                                     {'type': 'null'}],
                                                                                           'default': None,
                                                                                           'title': 'Width '
                                                                                                    'Max'},
                                                                             'height_min': {'anyOf': [{'type': 'integer'},
                                                                                                      {'type': 'null'}],
                                                                                            'default': None,
                                                                                            'title': 'Height '
                                                                                                     'Min'},
                                                                             'height_max': {'anyOf': [{'type': 'integer'},
                                                                                                      {'type': 'null'}],
                                                                                            'default': None,
                                                                                            'title': 'Height '
                                                                                                     'Max'},
                                                                             'scale_min': {'anyOf': [{'type': 'number'},
                                                                                                     {'type': 'null'}],
                                                                                           'default': None,
                                                                                           'description': 'Plate '
                                                                                                          'scale, '
                                                                                                          'arcsec/pixel.',
                                                                                           'title': 'Scale '
                                                                                                    'Min'},
                                                                             'scale_max': {'anyOf': [{'type': 'number'},
                                                                                                     {'type': 'null'}],
                                                                                           'default': None,
                                                                                           'title': 'Scale '
                                                                                                    'Max'},
                                                                             'star_count_min': {'anyOf': [{'type': 'integer'},
                                                                                                          {'type': 'null'}],
                                                                                                'default': None,
                                                                                                'title': 'Star '
                                                                                                         'Count '
                                                                                                         'Min'},
                                                                             'star_count_max': {'anyOf': [{'type': 'integer'},
                                                                                                          {'type': 'null'}],
                                                                                                'default': None,
                                                                                                'title': 'Star '
                                                                                                         'Count '
                                                                                                         'Max'},
                                                                             'fwhm_min': {'anyOf': [{'type': 'number'},
                                                                                                    {'type': 'null'}],
                                                                                          'default': None,
                                                                                          'title': 'Fwhm '
                                                                                                   'Min'},
                                                                             'fwhm_max': {'anyOf': [{'type': 'number'},
                                                                                                    {'type': 'null'}],
                                                                                          'default': None,
                                                                                          'title': 'Fwhm '
                                                                                                   'Max'},
                                                                             'background_min': {'anyOf': [{'type': 'number'},
                                                                                                          {'type': 'null'}],
                                                                                                'default': None,
                                                                                                'title': 'Background '
                                                                                                         'Min'},
                                                                             'background_max': {'anyOf': [{'type': 'number'},
                                                                                                          {'type': 'null'}],
                                                                                                'default': None,
                                                                                                'title': 'Background '
                                                                                                         'Max'},
                                                                             'background_rms_min': {'anyOf': [{'type': 'number'},
                                                                                                              {'type': 'null'}],
                                                                                                    'default': None,
                                                                                                    'title': 'Background '
                                                                                                             'Rms '
                                                                                                             'Min'},
                                                                             'background_rms_max': {'anyOf': [{'type': 'number'},
                                                                                                              {'type': 'null'}],
                                                                                                    'default': None,
                                                                                                    'title': 'Background '
                                                                                                             'Rms '
                                                                                                             'Max'},
                                                                             'elongation_min': {'anyOf': [{'type': 'number'},
                                                                                                          {'type': 'null'}],
                                                                                                'default': None,
                                                                                                'title': 'Elongation '
                                                                                                         'Min'},
                                                                             'elongation_max': {'anyOf': [{'type': 'number'},
                                                                                                          {'type': 'null'}],
                                                                                                'default': None,
                                                                                                'title': 'Elongation '
                                                                                                         'Max'},
                                                                             'sorting_field': {'anyOf': [{'enum': ['name',
                                                                                                                   'path',
                                                                                                                   'size',
                                                                                                                   'mtime',
                                                                                                                   'type',
                                                                                                                   'filter',
                                                                                                                   'exposure',
                                                                                                                   'gain',
                                                                                                                   'offset',
                                                                                                                   'binning',
                                                                                                                   'temperature',
                                                                                                                   'camera',
                                                                                                                   'telescope',
                                                                                                                   'object_name',
                                                                                                                   'date_obs',
                                                                                                                   'coord_ra',
                                                                                                                   'coord_dec',
                                                                                                                   'star_count',
                                                                                                                   'fwhm',
                                                                                                                   'elongation',
                                                                                                                   'background',
                                                                                                                   'background_rms'],
                                                                                                          'type': 'string'},
                                                                                                         {'type': 'null'}],
                                                                                               'default': None,
                                                                                               'description': 'Field '
                                                                                                              'to '
                                                                                                              'sort '
                                                                                                              'results '
                                                                                                              'by. '
                                                                                                              'Omit '
                                                                                                              'for '
                                                                                                              'the '
                                                                                                              'default '
                                                                                                              'root/path/name '
                                                                                                              'order.',
                                                                                               'title': 'Sorting '
                                                                                                        'Field'},
                                                                             'sorting_desc': {'anyOf': [{'type': 'boolean'},
                                                                                                        {'type': 'null'}],
                                                                                              'default': None,
                                                                                              'description': 'Sort '
                                                                                                             'direction '
                                                                                                             'for '
                                                                                                             '`sorting_field`; '
                                                                                                             'defaults '
                                                                                                             'to '
                                                                                                             'True '
                                                                                                             '(descending) '
                                                                                                             'if '
                                                                                                             'omitted.',
                                                                                              'title': 'Sorting '
                                                                                                       'Desc'}},
                                                              'title': 'SearchCriteriaInput',
                                                              'type': 'object'},
                                      '_RootAndPathInput': {'properties': {'root_id': {'description': 'A '
                                                                                                      'library '
                                                                                                      "root's "
                                                                                                      'rowid, '
                                                                                                      'from '
                                                                                                      '`list_library_roots`.',
                                                                                       'title': 'Root '
                                                                                                'Id',
                                                                                       'type': 'integer'},
                                                                           'path': {'anyOf': [{'type': 'string'},
                                                                                              {'type': 'null'}],
                                                                                    'default': None,
                                                                                    'description': 'Subdirectory '
                                                                                                   'prefix '
                                                                                                   'within '
                                                                                                   'the '
                                                                                                   'root '
                                                                                                   'to '
                                                                                                   'narrow '
                                                                                                   'the '
                                                                                                   'search. '
                                                                                                   'Omit '
                                                                                                   'to '
                                                                                                   'match '
                                                                                                   'the '
                                                                                                   'whole '
                                                                                                   'root.',
                                                                                    'title': 'Path'},
                                                                           'root_label': {'anyOf': [{'type': 'string'},
                                                                                                    {'type': 'null'}],
                                                                                          'default': None,
                                                                                          'description': 'Display-only; '
                                                                                                         'accepted '
                                                                                                         'for '
                                                                                                         'symmetry '
                                                                                                         'with '
                                                                                                         '`list_library_roots` '
                                                                                                         'output '
                                                                                                         'but '
                                                                                                         'not '
                                                                                                         'used '
                                                                                                         'for '
                                                                                                         'filtering. '
                                                                                                         'May '
                                                                                                         'be '
                                                                                                         'omitted.',
                                                                                          'title': 'Root '
                                                                                                   'Label'}},
                                                            'required': ['root_id'],
                                                            'title': '_RootAndPathInput',
                                                            'type': 'object'}},
                            'properties': {'criteria': {'anyOf': [{'$ref': '#/$defs/SearchCriteriaInput'},
                                                                  {'type': 'null'}],
                                                        'default': None},
                                           'page': {'default': 0,
                                                    'title': 'Page',
                                                    'type': 'integer'},
                                           'page_size': {'default': 100,
                                                         'title': 'Page Size',
                                                         'type': 'integer'},
                                           'include_paths': {'default': True,
                                                             'title': 'Include Paths',
                                                             'type': 'boolean'},
                                           'max_paths': {'default': 20,
                                                         'title': 'Max Paths',
                                                         'type': 'integer'}},
                            'title': 'report_targetsArguments',
                            'type': 'object'},
            'annotations': {'title': 'Target report',
                            'readOnlyHint': True,
                            'openWorldHint': False}},
           {'name': 'report_catalog_coverage',
            'description': "Which objects of a catalog the library's images actually cover.\n"
                           '\n'
                           "The same coverage report as the application's Catalog Report: each "
                           'catalog entry is\n'
                           'tested against the precise footprint of every matching image, not just '
                           'its centre.\n'
                           '`catalog` is one of the names from `list_catalogs`.\n'
                           '\n'
                           'Only **plate-solved** images are considered -- an unsolved library '
                           'yields an empty\n'
                           'report, so check `images_considered` before concluding you have not '
                           'imaged something.\n'
                           'This can take tens of seconds on a large library.\n'
                           '\n'
                           '`only_matching` defaults to true and returns just the covered objects. '
                           'Setting it\n'
                           'false also lists objects you have *not* imaged, which for a catalog '
                           'like NGC means\n'
                           'paging through ~785k rows; you almost never want it.\n'
                           '\n'
                           'Returns `{catalog, only_matching, results, page, page_size, total, '
                           'has_more,\n'
                           "images_considered, objects_matched}`. Each result's `files` entries "
                           'carry a `rowid`\n'
                           'usable with `get_file_details`, capped at `max_files` with a '
                           '`files_truncated` flag;\n'
                           'set `include_files=false` for a pure coverage list.\n',
            'inputSchema': {'$defs': {'SearchCriteriaInput': {'description': 'Search filters. Omit '
                                                                             'any field to leave '
                                                                             'it unconstrained.',
                                                              'properties': {'type': {'anyOf': [{'type': 'string'},
                                                                                                {'type': 'null'}],
                                                                                      'default': None,
                                                                                      'description': 'LIGHT/DARK/FLAT/BIAS/MASTER '
                                                                                                     '...',
                                                                                      'title': 'Type'},
                                                                             'filter': {'anyOf': [{'type': 'string'},
                                                                                                  {'type': 'null'}],
                                                                                        'default': None,
                                                                                        'title': 'Filter'},
                                                                             'camera': {'anyOf': [{'type': 'string'},
                                                                                                  {'type': 'null'}],
                                                                                        'default': None,
                                                                                        'title': 'Camera'},
                                                                             'telescope': {'anyOf': [{'type': 'string'},
                                                                                                     {'type': 'null'}],
                                                                                           'default': None,
                                                                                           'title': 'Telescope'},
                                                                             'object_name': {'anyOf': [{'type': 'string'},
                                                                                                       {'type': 'null'}],
                                                                                             'default': None,
                                                                                             'title': 'Object '
                                                                                                      'Name'},
                                                                             'file_name': {'anyOf': [{'type': 'string'},
                                                                                                     {'type': 'null'}],
                                                                                           'default': None,
                                                                                           'title': 'File '
                                                                                                    'Name'},
                                                                             'exposure': {'anyOf': [{'type': 'string'},
                                                                                                    {'type': 'null'}],
                                                                                          'default': None,
                                                                                          'description': 'Exposure '
                                                                                                         'time '
                                                                                                         'in '
                                                                                                         'seconds.',
                                                                                          'title': 'Exposure'},
                                                                             'exposure_tolerance': {'anyOf': [{'type': 'number'},
                                                                                                              {'type': 'null'}],
                                                                                                    'default': None,
                                                                                                    'description': '± '
                                                                                                                   'seconds '
                                                                                                                   'tolerance '
                                                                                                                   'for '
                                                                                                                   '`exposure`; '
                                                                                                                   'omit '
                                                                                                                   'for '
                                                                                                                   'an '
                                                                                                                   'exact '
                                                                                                                   'match.',
                                                                                                    'title': 'Exposure '
                                                                                                             'Tolerance'},
                                                                             'binning': {'anyOf': [{'type': 'string'},
                                                                                                   {'type': 'null'}],
                                                                                         'default': None,
                                                                                         'title': 'Binning'},
                                                                             'gain': {'anyOf': [{'type': 'string'},
                                                                                                {'type': 'null'}],
                                                                                      'default': None,
                                                                                      'title': 'Gain'},
                                                                             'offset': {'anyOf': [{'type': 'integer'},
                                                                                                  {'type': 'null'}],
                                                                                        'default': None,
                                                                                        'title': 'Offset'},
                                                                             'temperature': {'anyOf': [{'type': 'string'},
                                                                                                       {'type': 'null'}],
                                                                                             'default': None,
                                                                                             'title': 'Temperature'},
                                                                             'temperature_tolerance': {'anyOf': [{'type': 'number'},
                                                                                                                 {'type': 'null'}],
                                                                                                       'default': None,
                                                                                                       'description': '± '
                                                                                                                      '°C '
                                                                                                                      'tolerance '
                                                                                                                      'for '
                                                                                                                      '`temperature`; '
                                                                                                                      'omit '
                                                                                                                      'for '
                                                                                                                      'an '
                                                                                                                      'exact '
                                                                                                                      'match.',
                                                                                                       'title': 'Temperature '
                                                                                                                'Tolerance'},
                                                                             'coord_ra': {'anyOf': [{'type': 'string'},
                                                                                                    {'type': 'null'}],
                                                                                          'default': None,
                                                                                          'description': 'Right '
                                                                                                         'Ascension '
                                                                                                         'in '
                                                                                                         'hours.',
                                                                                          'title': 'Coord '
                                                                                                   'Ra'},
                                                                             'coord_dec': {'anyOf': [{'type': 'string'},
                                                                                                     {'type': 'null'}],
                                                                                           'default': None,
                                                                                           'description': 'Declination '
                                                                                                          'in '
                                                                                                          'degrees.',
                                                                                           'title': 'Coord '
                                                                                                    'Dec'},
                                                                             'coord_radius': {'anyOf': [{'type': 'number'},
                                                                                                        {'type': 'null'}],
                                                                                              'default': None,
                                                                                              'description': 'Cone '
                                                                                                             'search '
                                                                                                             'radius '
                                                                                                             'in '
                                                                                                             'decimal '
                                                                                                             'degrees '
                                                                                                             '(used '
                                                                                                             'with '
                                                                                                             '`coord_ra`/`coord_dec`).',
                                                                                              'title': 'Coord '
                                                                                                       'Radius'},
                                                                             'start_datetime': {'anyOf': [{'format': 'date-time',
                                                                                                           'type': 'string'},
                                                                                                          {'type': 'null'}],
                                                                                                'default': None,
                                                                                                'title': 'Start '
                                                                                                         'Datetime'},
                                                                             'end_datetime': {'anyOf': [{'format': 'date-time',
                                                                                                         'type': 'string'},
                                                                                                        {'type': 'null'}],
                                                                                              'default': None,
                                                                                              'title': 'End '
                                                                                                       'Datetime'},
                                                                             'header_text': {'anyOf': [{'type': 'string'},
                                                                                                       {'type': 'null'}],
                                                                                             'default': None,
                                                                                             'description': 'Free '
                                                                                                            'text '
                                                                                                            'or '
                                                                                                            'a '
                                                                                                            '"KEYWORD=value"/"KEYWORD<value" '
                                                                                                            'style '
                                                                                                            'FITS '
                                                                                                            'header '
                                                                                                            'match, '
                                                                                                            'e.g. '
                                                                                                            '"GAIN=100", '
                                                                                                            '"FOCTEMP<0".',
                                                                                             'title': 'Header '
                                                                                                      'Text'},
                                                                             'plate_solved': {'anyOf': [{'type': 'boolean'},
                                                                                                        {'type': 'null'}],
                                                                                              'default': None,
                                                                                              'description': 'True '
                                                                                                             '= '
                                                                                                             'solved '
                                                                                                             'only, '
                                                                                                             'False '
                                                                                                             '= '
                                                                                                             'unsolved '
                                                                                                             'only, '
                                                                                                             'omit '
                                                                                                             '= '
                                                                                                             'either.',
                                                                                              'title': 'Plate '
                                                                                                       'Solved'},
                                                                             'project': {'anyOf': [{'type': 'integer'},
                                                                                                   {'type': 'null'}],
                                                                                         'default': None,
                                                                                         'description': 'A '
                                                                                                        'project '
                                                                                                        'rowid '
                                                                                                        'from '
                                                                                                        '`list_projects`, '
                                                                                                        'or '
                                                                                                        '-1 '
                                                                                                        'to '
                                                                                                        'match '
                                                                                                        'files '
                                                                                                        'not '
                                                                                                        'assigned '
                                                                                                        'to '
                                                                                                        'any '
                                                                                                        'project.',
                                                                                         'title': 'Project'},
                                                                             'paths': {'anyOf': [{'items': {'$ref': '#/$defs/_RootAndPathInput'},
                                                                                                  'type': 'array'},
                                                                                                 {'type': 'null'}],
                                                                                       'default': None,
                                                                                       'description': 'Restrict '
                                                                                                      'the '
                                                                                                      'search '
                                                                                                      'to '
                                                                                                      'one '
                                                                                                      'or '
                                                                                                      'more '
                                                                                                      'library '
                                                                                                      'roots.',
                                                                                       'title': 'Paths'},
                                                                             'width_min': {'anyOf': [{'type': 'integer'},
                                                                                                     {'type': 'null'}],
                                                                                           'default': None,
                                                                                           'title': 'Width '
                                                                                                    'Min'},
                                                                             'width_max': {'anyOf': [{'type': 'integer'},
                                                                                                     {'type': 'null'}],
                                                                                           'default': None,
                                                                                           'title': 'Width '
                                                                                                    'Max'},
                                                                             'height_min': {'anyOf': [{'type': 'integer'},
                                                                                                      {'type': 'null'}],
                                                                                            'default': None,
                                                                                            'title': 'Height '
                                                                                                     'Min'},
                                                                             'height_max': {'anyOf': [{'type': 'integer'},
                                                                                                      {'type': 'null'}],
                                                                                            'default': None,
                                                                                            'title': 'Height '
                                                                                                     'Max'},
                                                                             'scale_min': {'anyOf': [{'type': 'number'},
                                                                                                     {'type': 'null'}],
                                                                                           'default': None,
                                                                                           'description': 'Plate '
                                                                                                          'scale, '
                                                                                                          'arcsec/pixel.',
                                                                                           'title': 'Scale '
                                                                                                    'Min'},
                                                                             'scale_max': {'anyOf': [{'type': 'number'},
                                                                                                     {'type': 'null'}],
                                                                                           'default': None,
                                                                                           'title': 'Scale '
                                                                                                    'Max'},
                                                                             'star_count_min': {'anyOf': [{'type': 'integer'},
                                                                                                          {'type': 'null'}],
                                                                                                'default': None,
                                                                                                'title': 'Star '
                                                                                                         'Count '
                                                                                                         'Min'},
                                                                             'star_count_max': {'anyOf': [{'type': 'integer'},
                                                                                                          {'type': 'null'}],
                                                                                                'default': None,
                                                                                                'title': 'Star '
                                                                                                         'Count '
                                                                                                         'Max'},
                                                                             'fwhm_min': {'anyOf': [{'type': 'number'},
                                                                                                    {'type': 'null'}],
                                                                                          'default': None,
                                                                                          'title': 'Fwhm '
                                                                                                   'Min'},
                                                                             'fwhm_max': {'anyOf': [{'type': 'number'},
                                                                                                    {'type': 'null'}],
                                                                                          'default': None,
                                                                                          'title': 'Fwhm '
                                                                                                   'Max'},
                                                                             'background_min': {'anyOf': [{'type': 'number'},
                                                                                                          {'type': 'null'}],
                                                                                                'default': None,
                                                                                                'title': 'Background '
                                                                                                         'Min'},
                                                                             'background_max': {'anyOf': [{'type': 'number'},
                                                                                                          {'type': 'null'}],
                                                                                                'default': None,
                                                                                                'title': 'Background '
                                                                                                         'Max'},
                                                                             'background_rms_min': {'anyOf': [{'type': 'number'},
                                                                                                              {'type': 'null'}],
                                                                                                    'default': None,
                                                                                                    'title': 'Background '
                                                                                                             'Rms '
                                                                                                             'Min'},
                                                                             'background_rms_max': {'anyOf': [{'type': 'number'},
                                                                                                              {'type': 'null'}],
                                                                                                    'default': None,
                                                                                                    'title': 'Background '
                                                                                                             'Rms '
                                                                                                             'Max'},
                                                                             'elongation_min': {'anyOf': [{'type': 'number'},
                                                                                                          {'type': 'null'}],
                                                                                                'default': None,
                                                                                                'title': 'Elongation '
                                                                                                         'Min'},
                                                                             'elongation_max': {'anyOf': [{'type': 'number'},
                                                                                                          {'type': 'null'}],
                                                                                                'default': None,
                                                                                                'title': 'Elongation '
                                                                                                         'Max'},
                                                                             'sorting_field': {'anyOf': [{'enum': ['name',
                                                                                                                   'path',
                                                                                                                   'size',
                                                                                                                   'mtime',
                                                                                                                   'type',
                                                                                                                   'filter',
                                                                                                                   'exposure',
                                                                                                                   'gain',
                                                                                                                   'offset',
                                                                                                                   'binning',
                                                                                                                   'temperature',
                                                                                                                   'camera',
                                                                                                                   'telescope',
                                                                                                                   'object_name',
                                                                                                                   'date_obs',
                                                                                                                   'coord_ra',
                                                                                                                   'coord_dec',
                                                                                                                   'star_count',
                                                                                                                   'fwhm',
                                                                                                                   'elongation',
                                                                                                                   'background',
                                                                                                                   'background_rms'],
                                                                                                          'type': 'string'},
                                                                                                         {'type': 'null'}],
                                                                                               'default': None,
                                                                                               'description': 'Field '
                                                                                                              'to '
                                                                                                              'sort '
                                                                                                              'results '
                                                                                                              'by. '
                                                                                                              'Omit '
                                                                                                              'for '
                                                                                                              'the '
                                                                                                              'default '
                                                                                                              'root/path/name '
                                                                                                              'order.',
                                                                                               'title': 'Sorting '
                                                                                                        'Field'},
                                                                             'sorting_desc': {'anyOf': [{'type': 'boolean'},
                                                                                                        {'type': 'null'}],
                                                                                              'default': None,
                                                                                              'description': 'Sort '
                                                                                                             'direction '
                                                                                                             'for '
                                                                                                             '`sorting_field`; '
                                                                                                             'defaults '
                                                                                                             'to '
                                                                                                             'True '
                                                                                                             '(descending) '
                                                                                                             'if '
                                                                                                             'omitted.',
                                                                                              'title': 'Sorting '
                                                                                                       'Desc'}},
                                                              'title': 'SearchCriteriaInput',
                                                              'type': 'object'},
                                      '_RootAndPathInput': {'properties': {'root_id': {'description': 'A '
                                                                                                      'library '
                                                                                                      "root's "
                                                                                                      'rowid, '
                                                                                                      'from '
                                                                                                      '`list_library_roots`.',
                                                                                       'title': 'Root '
                                                                                                'Id',
                                                                                       'type': 'integer'},
                                                                           'path': {'anyOf': [{'type': 'string'},
                                                                                              {'type': 'null'}],
                                                                                    'default': None,
                                                                                    'description': 'Subdirectory '
                                                                                                   'prefix '
                                                                                                   'within '
                                                                                                   'the '
                                                                                                   'root '
                                                                                                   'to '
                                                                                                   'narrow '
                                                                                                   'the '
                                                                                                   'search. '
                                                                                                   'Omit '
                                                                                                   'to '
                                                                                                   'match '
                                                                                                   'the '
                                                                                                   'whole '
                                                                                                   'root.',
                                                                                    'title': 'Path'},
                                                                           'root_label': {'anyOf': [{'type': 'string'},
                                                                                                    {'type': 'null'}],
                                                                                          'default': None,
                                                                                          'description': 'Display-only; '
                                                                                                         'accepted '
                                                                                                         'for '
                                                                                                         'symmetry '
                                                                                                         'with '
                                                                                                         '`list_library_roots` '
                                                                                                         'output '
                                                                                                         'but '
                                                                                                         'not '
                                                                                                         'used '
                                                                                                         'for '
                                                                                                         'filtering. '
                                                                                                         'May '
                                                                                                         'be '
                                                                                                         'omitted.',
                                                                                          'title': 'Root '
                                                                                                   'Label'}},
                                                            'required': ['root_id'],
                                                            'title': '_RootAndPathInput',
                                                            'type': 'object'}},
                            'properties': {'catalog': {'title': 'Catalog', 'type': 'string'},
                                           'criteria': {'anyOf': [{'$ref': '#/$defs/SearchCriteriaInput'},
                                                                  {'type': 'null'}],
                                                        'default': None},
                                           'only_matching': {'default': True,
                                                             'title': 'Only Matching',
                                                             'type': 'boolean'},
                                           'page': {'default': 0,
                                                    'title': 'Page',
                                                    'type': 'integer'},
                                           'page_size': {'default': 100,
                                                         'title': 'Page Size',
                                                         'type': 'integer'},
                                           'include_files': {'default': True,
                                                             'title': 'Include Files',
                                                             'type': 'boolean'},
                                           'max_files': {'default': 10,
                                                         'title': 'Max Files',
                                                         'type': 'integer'}},
                            'required': ['catalog'],
                            'title': 'report_catalog_coverageArguments',
                            'type': 'object'},
            'annotations': {'title': 'Catalog coverage report',
                            'readOnlyHint': True,
                            'openWorldHint': False}},
           {'name': 'get_header_values',
            'description': 'Read the same few fields from many files at once.\n'
                           '\n'
                           "The bulk form of `get_file_details`: instead of one file's entire "
                           'header, this returns only the `fields` you name, across every file '
                           'matching `criteria`. Use it for questions like "what was FOCTEMP '
                           'across last night\'s subs".\n'
                           '\n'
                           'Each entry of `fields` names its source by prefix:\n'
                           '- `"File.size"`, `"Image.exposure"` -- a PhotonFinder model field\n'
                           '- `"WCS:CRVAL1"` -- a keyword from the plate-solve solution\n'
                           '- anything else, e.g. `"FOCTEMP"` -- a FITS header keyword\n'
                           '\n'
                           'The `WCS:` prefix matters because keywords like `NAXIS1` and `CRVAL1` '
                           'exist in both the original header and the solved one. To discover what '
                           'a file carries, call `get_file_details` on one representative file and '
                           'read its `header`.\n'
                           '\n'
                           'At most 20 fields per call. A field a file does not have comes back as '
                           'null rather than an error. Returns {results, page, page_size, total, '
                           'has_more} with one {rowid, full_filename, values} entry per file.',
            'inputSchema': {'$defs': {'SearchCriteriaInput': {'description': 'Search filters. Omit '
                                                                             'any field to leave '
                                                                             'it unconstrained.',
                                                              'properties': {'type': {'anyOf': [{'type': 'string'},
                                                                                                {'type': 'null'}],
                                                                                      'default': None,
                                                                                      'description': 'LIGHT/DARK/FLAT/BIAS/MASTER '
                                                                                                     '...',
                                                                                      'title': 'Type'},
                                                                             'filter': {'anyOf': [{'type': 'string'},
                                                                                                  {'type': 'null'}],
                                                                                        'default': None,
                                                                                        'title': 'Filter'},
                                                                             'camera': {'anyOf': [{'type': 'string'},
                                                                                                  {'type': 'null'}],
                                                                                        'default': None,
                                                                                        'title': 'Camera'},
                                                                             'telescope': {'anyOf': [{'type': 'string'},
                                                                                                     {'type': 'null'}],
                                                                                           'default': None,
                                                                                           'title': 'Telescope'},
                                                                             'object_name': {'anyOf': [{'type': 'string'},
                                                                                                       {'type': 'null'}],
                                                                                             'default': None,
                                                                                             'title': 'Object '
                                                                                                      'Name'},
                                                                             'file_name': {'anyOf': [{'type': 'string'},
                                                                                                     {'type': 'null'}],
                                                                                           'default': None,
                                                                                           'title': 'File '
                                                                                                    'Name'},
                                                                             'exposure': {'anyOf': [{'type': 'string'},
                                                                                                    {'type': 'null'}],
                                                                                          'default': None,
                                                                                          'description': 'Exposure '
                                                                                                         'time '
                                                                                                         'in '
                                                                                                         'seconds.',
                                                                                          'title': 'Exposure'},
                                                                             'exposure_tolerance': {'anyOf': [{'type': 'number'},
                                                                                                              {'type': 'null'}],
                                                                                                    'default': None,
                                                                                                    'description': '± '
                                                                                                                   'seconds '
                                                                                                                   'tolerance '
                                                                                                                   'for '
                                                                                                                   '`exposure`; '
                                                                                                                   'omit '
                                                                                                                   'for '
                                                                                                                   'an '
                                                                                                                   'exact '
                                                                                                                   'match.',
                                                                                                    'title': 'Exposure '
                                                                                                             'Tolerance'},
                                                                             'binning': {'anyOf': [{'type': 'string'},
                                                                                                   {'type': 'null'}],
                                                                                         'default': None,
                                                                                         'title': 'Binning'},
                                                                             'gain': {'anyOf': [{'type': 'string'},
                                                                                                {'type': 'null'}],
                                                                                      'default': None,
                                                                                      'title': 'Gain'},
                                                                             'offset': {'anyOf': [{'type': 'integer'},
                                                                                                  {'type': 'null'}],
                                                                                        'default': None,
                                                                                        'title': 'Offset'},
                                                                             'temperature': {'anyOf': [{'type': 'string'},
                                                                                                       {'type': 'null'}],
                                                                                             'default': None,
                                                                                             'title': 'Temperature'},
                                                                             'temperature_tolerance': {'anyOf': [{'type': 'number'},
                                                                                                                 {'type': 'null'}],
                                                                                                       'default': None,
                                                                                                       'description': '± '
                                                                                                                      '°C '
                                                                                                                      'tolerance '
                                                                                                                      'for '
                                                                                                                      '`temperature`; '
                                                                                                                      'omit '
                                                                                                                      'for '
                                                                                                                      'an '
                                                                                                                      'exact '
                                                                                                                      'match.',
                                                                                                       'title': 'Temperature '
                                                                                                                'Tolerance'},
                                                                             'coord_ra': {'anyOf': [{'type': 'string'},
                                                                                                    {'type': 'null'}],
                                                                                          'default': None,
                                                                                          'description': 'Right '
                                                                                                         'Ascension '
                                                                                                         'in '
                                                                                                         'hours.',
                                                                                          'title': 'Coord '
                                                                                                   'Ra'},
                                                                             'coord_dec': {'anyOf': [{'type': 'string'},
                                                                                                     {'type': 'null'}],
                                                                                           'default': None,
                                                                                           'description': 'Declination '
                                                                                                          'in '
                                                                                                          'degrees.',
                                                                                           'title': 'Coord '
                                                                                                    'Dec'},
                                                                             'coord_radius': {'anyOf': [{'type': 'number'},
                                                                                                        {'type': 'null'}],
                                                                                              'default': None,
                                                                                              'description': 'Cone '
                                                                                                             'search '
                                                                                                             'radius '
                                                                                                             'in '
                                                                                                             'decimal '
                                                                                                             'degrees '
                                                                                                             '(used '
                                                                                                             'with '
                                                                                                             '`coord_ra`/`coord_dec`).',
                                                                                              'title': 'Coord '
                                                                                                       'Radius'},
                                                                             'start_datetime': {'anyOf': [{'format': 'date-time',
                                                                                                           'type': 'string'},
                                                                                                          {'type': 'null'}],
                                                                                                'default': None,
                                                                                                'title': 'Start '
                                                                                                         'Datetime'},
                                                                             'end_datetime': {'anyOf': [{'format': 'date-time',
                                                                                                         'type': 'string'},
                                                                                                        {'type': 'null'}],
                                                                                              'default': None,
                                                                                              'title': 'End '
                                                                                                       'Datetime'},
                                                                             'header_text': {'anyOf': [{'type': 'string'},
                                                                                                       {'type': 'null'}],
                                                                                             'default': None,
                                                                                             'description': 'Free '
                                                                                                            'text '
                                                                                                            'or '
                                                                                                            'a '
                                                                                                            '"KEYWORD=value"/"KEYWORD<value" '
                                                                                                            'style '
                                                                                                            'FITS '
                                                                                                            'header '
                                                                                                            'match, '
                                                                                                            'e.g. '
                                                                                                            '"GAIN=100", '
                                                                                                            '"FOCTEMP<0".',
                                                                                             'title': 'Header '
                                                                                                      'Text'},
                                                                             'plate_solved': {'anyOf': [{'type': 'boolean'},
                                                                                                        {'type': 'null'}],
                                                                                              'default': None,
                                                                                              'description': 'True '
                                                                                                             '= '
                                                                                                             'solved '
                                                                                                             'only, '
                                                                                                             'False '
                                                                                                             '= '
                                                                                                             'unsolved '
                                                                                                             'only, '
                                                                                                             'omit '
                                                                                                             '= '
                                                                                                             'either.',
                                                                                              'title': 'Plate '
                                                                                                       'Solved'},
                                                                             'project': {'anyOf': [{'type': 'integer'},
                                                                                                   {'type': 'null'}],
                                                                                         'default': None,
                                                                                         'description': 'A '
                                                                                                        'project '
                                                                                                        'rowid '
                                                                                                        'from '
                                                                                                        '`list_projects`, '
                                                                                                        'or '
                                                                                                        '-1 '
                                                                                                        'to '
                                                                                                        'match '
                                                                                                        'files '
                                                                                                        'not '
                                                                                                        'assigned '
                                                                                                        'to '
                                                                                                        'any '
                                                                                                        'project.',
                                                                                         'title': 'Project'},
                                                                             'paths': {'anyOf': [{'items': {'$ref': '#/$defs/_RootAndPathInput'},
                                                                                                  'type': 'array'},
                                                                                                 {'type': 'null'}],
                                                                                       'default': None,
                                                                                       'description': 'Restrict '
                                                                                                      'the '
                                                                                                      'search '
                                                                                                      'to '
                                                                                                      'one '
                                                                                                      'or '
                                                                                                      'more '
                                                                                                      'library '
                                                                                                      'roots.',
                                                                                       'title': 'Paths'},
                                                                             'width_min': {'anyOf': [{'type': 'integer'},
                                                                                                     {'type': 'null'}],
                                                                                           'default': None,
                                                                                           'title': 'Width '
                                                                                                    'Min'},
                                                                             'width_max': {'anyOf': [{'type': 'integer'},
                                                                                                     {'type': 'null'}],
                                                                                           'default': None,
                                                                                           'title': 'Width '
                                                                                                    'Max'},
                                                                             'height_min': {'anyOf': [{'type': 'integer'},
                                                                                                      {'type': 'null'}],
                                                                                            'default': None,
                                                                                            'title': 'Height '
                                                                                                     'Min'},
                                                                             'height_max': {'anyOf': [{'type': 'integer'},
                                                                                                      {'type': 'null'}],
                                                                                            'default': None,
                                                                                            'title': 'Height '
                                                                                                     'Max'},
                                                                             'scale_min': {'anyOf': [{'type': 'number'},
                                                                                                     {'type': 'null'}],
                                                                                           'default': None,
                                                                                           'description': 'Plate '
                                                                                                          'scale, '
                                                                                                          'arcsec/pixel.',
                                                                                           'title': 'Scale '
                                                                                                    'Min'},
                                                                             'scale_max': {'anyOf': [{'type': 'number'},
                                                                                                     {'type': 'null'}],
                                                                                           'default': None,
                                                                                           'title': 'Scale '
                                                                                                    'Max'},
                                                                             'star_count_min': {'anyOf': [{'type': 'integer'},
                                                                                                          {'type': 'null'}],
                                                                                                'default': None,
                                                                                                'title': 'Star '
                                                                                                         'Count '
                                                                                                         'Min'},
                                                                             'star_count_max': {'anyOf': [{'type': 'integer'},
                                                                                                          {'type': 'null'}],
                                                                                                'default': None,
                                                                                                'title': 'Star '
                                                                                                         'Count '
                                                                                                         'Max'},
                                                                             'fwhm_min': {'anyOf': [{'type': 'number'},
                                                                                                    {'type': 'null'}],
                                                                                          'default': None,
                                                                                          'title': 'Fwhm '
                                                                                                   'Min'},
                                                                             'fwhm_max': {'anyOf': [{'type': 'number'},
                                                                                                    {'type': 'null'}],
                                                                                          'default': None,
                                                                                          'title': 'Fwhm '
                                                                                                   'Max'},
                                                                             'background_min': {'anyOf': [{'type': 'number'},
                                                                                                          {'type': 'null'}],
                                                                                                'default': None,
                                                                                                'title': 'Background '
                                                                                                         'Min'},
                                                                             'background_max': {'anyOf': [{'type': 'number'},
                                                                                                          {'type': 'null'}],
                                                                                                'default': None,
                                                                                                'title': 'Background '
                                                                                                         'Max'},
                                                                             'background_rms_min': {'anyOf': [{'type': 'number'},
                                                                                                              {'type': 'null'}],
                                                                                                    'default': None,
                                                                                                    'title': 'Background '
                                                                                                             'Rms '
                                                                                                             'Min'},
                                                                             'background_rms_max': {'anyOf': [{'type': 'number'},
                                                                                                              {'type': 'null'}],
                                                                                                    'default': None,
                                                                                                    'title': 'Background '
                                                                                                             'Rms '
                                                                                                             'Max'},
                                                                             'elongation_min': {'anyOf': [{'type': 'number'},
                                                                                                          {'type': 'null'}],
                                                                                                'default': None,
                                                                                                'title': 'Elongation '
                                                                                                         'Min'},
                                                                             'elongation_max': {'anyOf': [{'type': 'number'},
                                                                                                          {'type': 'null'}],
                                                                                                'default': None,
                                                                                                'title': 'Elongation '
                                                                                                         'Max'},
                                                                             'sorting_field': {'anyOf': [{'enum': ['name',
                                                                                                                   'path',
                                                                                                                   'size',
                                                                                                                   'mtime',
                                                                                                                   'type',
                                                                                                                   'filter',
                                                                                                                   'exposure',
                                                                                                                   'gain',
                                                                                                                   'offset',
                                                                                                                   'binning',
                                                                                                                   'temperature',
                                                                                                                   'camera',
                                                                                                                   'telescope',
                                                                                                                   'object_name',
                                                                                                                   'date_obs',
                                                                                                                   'coord_ra',
                                                                                                                   'coord_dec',
                                                                                                                   'star_count',
                                                                                                                   'fwhm',
                                                                                                                   'elongation',
                                                                                                                   'background',
                                                                                                                   'background_rms'],
                                                                                                          'type': 'string'},
                                                                                                         {'type': 'null'}],
                                                                                               'default': None,
                                                                                               'description': 'Field '
                                                                                                              'to '
                                                                                                              'sort '
                                                                                                              'results '
                                                                                                              'by. '
                                                                                                              'Omit '
                                                                                                              'for '
                                                                                                              'the '
                                                                                                              'default '
                                                                                                              'root/path/name '
                                                                                                              'order.',
                                                                                               'title': 'Sorting '
                                                                                                        'Field'},
                                                                             'sorting_desc': {'anyOf': [{'type': 'boolean'},
                                                                                                        {'type': 'null'}],
                                                                                              'default': None,
                                                                                              'description': 'Sort '
                                                                                                             'direction '
                                                                                                             'for '
                                                                                                             '`sorting_field`; '
                                                                                                             'defaults '
                                                                                                             'to '
                                                                                                             'True '
                                                                                                             '(descending) '
                                                                                                             'if '
                                                                                                             'omitted.',
                                                                                              'title': 'Sorting '
                                                                                                       'Desc'}},
                                                              'title': 'SearchCriteriaInput',
                                                              'type': 'object'},
                                      '_RootAndPathInput': {'properties': {'root_id': {'description': 'A '
                                                                                                      'library '
                                                                                                      "root's "
                                                                                                      'rowid, '
                                                                                                      'from '
                                                                                                      '`list_library_roots`.',
                                                                                       'title': 'Root '
                                                                                                'Id',
                                                                                       'type': 'integer'},
                                                                           'path': {'anyOf': [{'type': 'string'},
                                                                                              {'type': 'null'}],
                                                                                    'default': None,
                                                                                    'description': 'Subdirectory '
                                                                                                   'prefix '
                                                                                                   'within '
                                                                                                   'the '
                                                                                                   'root '
                                                                                                   'to '
                                                                                                   'narrow '
                                                                                                   'the '
                                                                                                   'search. '
                                                                                                   'Omit '
                                                                                                   'to '
                                                                                                   'match '
                                                                                                   'the '
                                                                                                   'whole '
                                                                                                   'root.',
                                                                                    'title': 'Path'},
                                                                           'root_label': {'anyOf': [{'type': 'string'},
                                                                                                    {'type': 'null'}],
                                                                                          'default': None,
                                                                                          'description': 'Display-only; '
                                                                                                         'accepted '
                                                                                                         'for '
                                                                                                         'symmetry '
                                                                                                         'with '
                                                                                                         '`list_library_roots` '
                                                                                                         'output '
                                                                                                         'but '
                                                                                                         'not '
                                                                                                         'used '
                                                                                                         'for '
                                                                                                         'filtering. '
                                                                                                         'May '
                                                                                                         'be '
                                                                                                         'omitted.',
                                                                                          'title': 'Root '
                                                                                                   'Label'}},
                                                            'required': ['root_id'],
                                                            'title': '_RootAndPathInput',
                                                            'type': 'object'}},
                            'properties': {'fields': {'items': {'type': 'string'},
                                                      'title': 'Fields',
                                                      'type': 'array'},
                                           'criteria': {'anyOf': [{'$ref': '#/$defs/SearchCriteriaInput'},
                                                                  {'type': 'null'}],
                                                        'default': None},
                                           'page': {'default': 0,
                                                    'title': 'Page',
                                                    'type': 'integer'},
                                           'page_size': {'default': 100,
                                                         'title': 'Page Size',
                                                         'type': 'integer'}},
                            'required': ['fields'],
                            'title': 'get_header_valuesArguments',
                            'type': 'object'},
            'annotations': {'title': 'Header values',
                            'readOnlyHint': True,
                            'openWorldHint': False}},
           {'name': 'list_library_roots',
            'description': 'List the configured library roots (top-level scanned directories).\n'
                           '\n'
                           'Each root has a `rowid` (use it in `search_files` via `paths`), a '
                           '`name`, its\n'
                           '`path` on disk, and a user-written `description` of what it holds (may '
                           'be null).\n'
                           'Read the descriptions before searching -- they could describe the '
                           'contents, so you\n'
                           'can scope a search instead of scanning the whole library.',
            'inputSchema': {'properties': {},
                            'title': 'list_library_rootsArguments',
                            'type': 'object'},
            'outputSchema': {'properties': {'result': {'items': {'additionalProperties': True,
                                                                 'type': 'object'},
                                                       'title': 'Result',
                                                       'type': 'array'}},
                             'required': ['result'],
                             'title': 'list_library_rootsOutput',
                             'type': 'object'},
            'annotations': {'title': 'List library roots',
                            'readOnlyHint': True,
                            'openWorldHint': False}},
           {'name': 'list_projects',
            'description': 'List projects defined in the library, with summary info per project:\n'
                           "`file_count`, `last_date_obs` (most recent image's observation time), "
                           'and the\n'
                           '`coord_ra`/`coord_dec` (degrees) of its most recent image. Use the '
                           '`rowid` with\n'
                           '`search_files` (`criteria={"project": rowid}`) to list a project\'s '
                           'files, or with\n'
                           '`get_project_details` for the same summary for a single project.',
            'inputSchema': {'properties': {}, 'title': 'list_projectsArguments', 'type': 'object'},
            'outputSchema': {'properties': {'result': {'items': {'additionalProperties': True,
                                                                 'type': 'object'},
                                                       'title': 'Result',
                                                       'type': 'array'}},
                             'required': ['result'],
                             'title': 'list_projectsOutput',
                             'type': 'object'},
            'annotations': {'title': 'List projects',
                            'readOnlyHint': True,
                            'openWorldHint': False}},
           {'name': 'get_project_details',
            'description': 'Get summary details for a single project by its rowid: `file_count`,\n'
                           '`last_date_obs`, and `coord_ra`/`coord_dec` of its most recent image.',
            'inputSchema': {'properties': {'rowid': {'title': 'Rowid', 'type': 'integer'}},
                            'required': ['rowid'],
                            'title': 'get_project_detailsArguments',
                            'type': 'object'},
            'annotations': {'title': 'Project details',
                            'readOnlyHint': True,
                            'openWorldHint': False}},
           {'name': 'list_distinct_values',
            'description': 'List the distinct values present for a field, to help build search '
                           'criteria.\n'
                           '\n'
                           '`field` must be one of: filter, type, camera, telescope, '
                           'object_name.\n',
            'inputSchema': {'properties': {'field': {'title': 'Field', 'type': 'string'}},
                            'required': ['field'],
                            'title': 'list_distinct_valuesArguments',
                            'type': 'object'},
            'annotations': {'title': 'List distinct values',
                            'readOnlyHint': True,
                            'openWorldHint': False}},
           {'name': 'get_file_details',
            'description': 'Get full metadata for a single file by its rowid, including the '
                           'decompressed\n'
                           'FITS header keywords and plate-solve status. Use the `rowid` from '
                           '`search_files`\n'
                           'results.',
            'inputSchema': {'properties': {'rowid': {'title': 'Rowid', 'type': 'integer'}},
                            'required': ['rowid'],
                            'title': 'get_file_detailsArguments',
                            'type': 'object'},
            'annotations': {'title': 'File details', 'readOnlyHint': True, 'openWorldHint': False}},
           {'name': 'list_catalogs',
            'description': 'List the local catalog names available for `lookup_object` (e.g. '
                           '"NGC", "IC",\n'
                           '"M"). Backed entirely by PhotonFinder\'s local catalog database; '
                           'performs no\n'
                           'online lookups.',
            'inputSchema': {'properties': {}, 'title': 'list_catalogsArguments', 'type': 'object'},
            'outputSchema': {'properties': {'result': {'items': {'type': 'string'},
                                                       'title': 'Result',
                                                       'type': 'array'}},
                             'required': ['result'],
                             'title': 'list_catalogsOutput',
                             'type': 'object'},
            'annotations': {'title': 'List catalogs',
                            'readOnlyHint': True,
                            'openWorldHint': False}},
           {'name': 'lookup_object',
            'description': "Resolve an object's RA/Dec (degrees) from PhotonFinder's local "
                           'catalog\n'
                           'database only -- no online services (Simbad, Telescopius, etc.) are '
                           'contacted.\n'
                           '\n'
                           '`catalog` must be one of the names returned by `list_catalogs`. '
                           '`catalog_id` is\n'
                           "matched against either the catalog's own identifier or its canonical "
                           'identifier\n'
                           '(e.g. catalog="NGC", catalog_id="7000").\n'
                           '\n'
                           'Returns object fields (ra, dec, size, axis_ratio, angle, magnitude) '
                           'or\n'
                           '`{"error": ...}` if no match is found.\n',
            'inputSchema': {'properties': {'catalog': {'title': 'Catalog', 'type': 'string'},
                                           'catalog_id': {'title': 'Catalog Id', 'type': 'string'}},
                            'required': ['catalog', 'catalog_id'],
                            'title': 'lookup_objectArguments',
                            'type': 'object'},
            'annotations': {'title': 'Look up object',
                            'readOnlyHint': True,
                            'openWorldHint': False}},
           {'name': 'plate_solve_files',
            'description': 'Plate-solve up to 10 files at once by rowid (from '
                           '`search_files`/`get_file_details`), writing the resulting WCS solution '
                           'and coordinates to the library. `solver`/`backup_solver` are one of '
                           '"astap", "astrometry_net", "solve_field"; omit to use the '
                           'primary/backup solver configured in Settings (no backup if unset). '
                           '`hint_ra`/`hint_dec` (degrees) and `hint_scale` (arcsec/pixel) seed '
                           'the solver; `hint_mode` is "fallback" (only used if the file has no '
                           'usable coordinates/scale) or "override" (always used). Omitted hint '
                           'fields fall back to the values configured in Settings. Returns '
                           '{results, solved, failed}. `results` is a list of per-file dicts with '
                           '`rowid`, `path`, `success`, and on success '
                           "`ra`/`dec`/`scale_arcsec`/`solver`, or on failure `error`. One file's "
                           'failure does not abort the rest of the batch. Returns {"error": ...} '
                           'if the batch is empty, too large, if a plate-solve is already running '
                           '(from this application or another request), or if the user has not '
                           'enabled agent-triggered plate solving in Settings.',
            'inputSchema': {'properties': {'rowids': {'items': {'type': 'integer'},
                                                      'title': 'Rowids',
                                                      'type': 'array'},
                                           'solver': {'anyOf': [{'type': 'string'},
                                                                {'type': 'null'}],
                                                      'default': None,
                                                      'title': 'Solver'},
                                           'backup_solver': {'anyOf': [{'type': 'string'},
                                                                       {'type': 'null'}],
                                                             'default': None,
                                                             'title': 'Backup Solver'},
                                           'hint_ra': {'anyOf': [{'type': 'number'},
                                                                 {'type': 'null'}],
                                                       'default': None,
                                                       'title': 'Hint Ra'},
                                           'hint_dec': {'anyOf': [{'type': 'number'},
                                                                  {'type': 'null'}],
                                                        'default': None,
                                                        'title': 'Hint Dec'},
                                           'hint_scale': {'anyOf': [{'type': 'number'},
                                                                    {'type': 'null'}],
                                                          'default': None,
                                                          'title': 'Hint Scale'},
                                           'hint_mode': {'anyOf': [{'type': 'string'},
                                                                   {'type': 'null'}],
                                                         'default': None,
                                                         'title': 'Hint Mode'}},
                            'required': ['rowids'],
                            'title': 'plate_solve_filesArguments',
                            'type': 'object'},
            'annotations': {'title': 'Plate-solve files',
                            'readOnlyHint': False,
                            'destructiveHint': False,
                            'idempotentHint': True,
                            'openWorldHint': True}}]}
