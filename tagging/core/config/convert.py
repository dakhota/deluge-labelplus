#
# convert.py
#
# Copyright (C) 2014 Ratanak Lun <ratanakvlun@gmail.com>
#
# This module is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This software is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#
# Linking this software with other modules is making a combined work
# based on this software. Thus, the terms and conditions of the GNU
# General Public License cover the whole combination.
#
# As a special exception, the copyright holders of this software give
# you permission to link this software with independent modules to
# produce a combined work, regardless of the license terms of these
# independent modules, and to copy and distribute the resulting work
# under terms of your choice, provided that you also meet, for each
# linked module in the combined work, the terms and conditions of the
# license of that module. An independent module is a module which is
# not derived from or based on this software. If you modify this
# software, you may extend this exception to your version of the
# software, but you are not obligated to do so. If you do not wish to
# do so, delete this exception statement from your version.
#


import tagging.common.tag
import tagging.common.config
import tagging.common.config.autotag


def post_map_v1_v2(spec, dict_in):

  def remove_v1_prefix(dict_in):

    tags = dict_in["tags"]
    for id in tags.keys():
      if id.startswith("-"):
        data = tags[id]
        del tags[id]

        new_id = id.partition(":")[2]
        tags[new_id] = data

    mappings = dict_in["mappings"]
    for id in mappings:
      tag_id = mappings[id]
      if tag_id.startswith("-"):
        mappings[id] = tag_id.partition(":")[2]


  def convert_auto_queries(dict_in, op):

    rules = []
    case = tagging.common.config.autotag.CASE_MATCH

    if dict_in["auto_tracker"]:
      prop = tagging.common.config.autotag.PROP_TRACKER
    else:
      prop = tagging.common.config.autotag.PROP_NAME

    for line in dict_in["auto_queries"]:
      rules.append([prop, op, case, line])

    dict_in["autotag_rules"] = rules
    dict_in["autotag_match_all"] = False


  tag_defaults = dict_in["prefs"]["tag"]
  option_defaults = dict_in["prefs"]["options"]

  remove_v1_prefix(dict_in)

  op = tagging.common.config.autotag.OP_CONTAINS_WORDS
  if option_defaults.get("autotag_uses_regex"):
    op = tagging.common.config.autotag.OP_MATCHES_REGEX

  convert_auto_queries(tag_defaults, op)

  tags = dict_in["tags"]
  for id in tags.keys():
    if id in tagging.common.tag.RESERVED_IDS:
      del tags[id]
      continue

    convert_auto_queries(tags[id]["options"], op)

  return dict_in


CONFIG_SPEC_V1_V2 = {
  "version_in": 1,
  "version_out": 2,
  "defaults": tagging.common.config.CONFIG_DEFAULTS_V2,
  "strict": False,
  "deepcopy": False,
  "post_func": post_map_v1_v2,
  "map": {
    "prefs/options": "prefs/options",
    "prefs/options/shared_limit_update_interval":
      "prefs/options/shared_limit_interval",

    "prefs/defaults": "prefs/tag",
    "prefs/defaults/move_data_completed":
      "prefs/tag/move_completed",
    "prefs/defaults/move_data_completed_path":
      "prefs/tag/move_completed_path",
    "prefs/defaults/move_data_completed_mode":
      "prefs/tag/move_completed_mode",
    "prefs/defaults/shared_limit_on":
      "prefs/tag/shared_limit",
    "prefs/defaults/auto_settings":
      "prefs/tag/autotag_settings",

    "tags/*/name": "tags/*/name",
    "tags/*/data": "tags/*/options",
    "tags/*/data/move_data_completed":
      "tags/*/options/move_completed",
    "tags/*/data/move_data_completed_path":
      "tags/*/options/move_completed_path",
    "tags/*/data/move_data_completed_mode":
      "tags/*/options/move_completed_mode",
    "tags/*/data/shared_limit_on":
      "tags/*/options/shared_limit",
    "tags/*/data/auto_settings":
      "tags/*/options/autotag_settings",

    "mappings": "mappings",
  },
}

CONFIG_SPECS = {
  (1, 2): CONFIG_SPEC_V1_V2,
}
