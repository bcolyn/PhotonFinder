"""Tool manifest served by the MCP stub. GENERATED -- do not edit by hand.

Regenerate with `uv run python scripts/build_mcp_manifest.py` after changing the tools in
`mcp_server.py`; `test_manifest_is_up_to_date` fails if this drifts.

Kept as a Python module rather than a data file so the stub can import it with no file IO
and no PyInstaller `datas` entry, working identically frozen and from source.
"""

MANIFEST = {'instructions': 'PhotonFinder manages an astrophotography file library (FITS/XISF images and '
                 'calibration frames). Use `search_files` with a SearchCriteria JSON object to '
                 'find files; use `list_library_roots`, `list_projects` and `list_distinct_values` '
                 'to discover valid filter values, `get_project_details` to inspect a single '
                 "project, `get_file_details` to inspect one file's full metadata and FITS header, "
                 "and `lookup_object`/`list_catalogs` to resolve an object's RA/Dec from "
                 "PhotonFinder's local catalog database (no online lookups such as Simbad or "
                 'Telescopius are performed). Every one of these is read-only. `plate_solve_files` '
                 'is the sole exception and the user must opt into it in Settings; it plate-solves '
                 'up to 10 files at once and writes the resulting WCS/coordinates to the library.',
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
           {'name': 'list_library_roots',
            'description': 'List the configured library roots (top-level scanned directories).',
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
