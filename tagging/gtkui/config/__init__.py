#
# __init__.py
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


from tagging.common.tag import ID_ALL


# Version 1

CONFIG_DEFAULTS_V1 = {
  "name_input_size": None,
  "name_input_pos": None,
  "tag_options_size": None,
  "tag_options_pos": None,

  "prefs_state": [],
  "sidebar_state": {
    "selected": ID_ALL,
    "expanded": [],
  },

  "show_tag_bandwidth": False,
}


# Version 2

CONFIG_DEFAULTS_V2 = {
  "common": {
    "name_input_size": None,
    "name_input_pos": None,
    "tag_options_size": None,
    "tag_options_pos": None,

    "prefs_state": [],

    "show_tag_bandwidth": False,
    "status_include_subtag": False,
  },
  "daemon": {},
}

DAEMON_DEFAULTS_V2 = {
  "sidebar_state": {
    "selected": ID_ALL,
    "expanded": [],
  },
}

# Version 3

CONFIG_DEFAULTS_V3 = {
  "common": {
    "name_input_size": None,
    "name_input_pos": None,
    "name_input_fullname": False,

    "tag_options_size": None,
    "tag_options_pos": None,
    "tag_options_fullname": False,
    "tag_options_pane_pos": -1,

    "prefs_state": [],
    "prefs_pane_pos": -1,

    "status_bar": False,
    "status_bar_include_subtags": False,

    "add_torrent_ext_fullname": False,
    "torrent_view_fullname": False,

    "filter_include_subtags": False,
  },
  "daemon": {},
}

DAEMON_DEFAULTS_V3 = {
  "sidebar_state": {
    "selected": [ID_ALL],
    "expanded": [],
  },
}


# Current Version

CONFIG_VERSION = 3
CONFIG_DEFAULTS = CONFIG_DEFAULTS_V3
DAEMON_DEFAULTS = DAEMON_DEFAULTS_V3
