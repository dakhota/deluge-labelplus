/*
Script: tagging.js
    The client-side javascript code for the Tagging plugin.

Copyright:
    (C) Ratanak Lun 2014 <ratanakvlun@gmail.com>
    This program is free software; you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation; either version 3, or (at your option)
    any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, write to:
        The Free Software Foundation, Inc.,
        51 Franklin Street, Fifth Floor
        Boston, MA  02110-1301, USA.

    In addition, as a special exception, the copyright holders give
    permission to link the code of portions of this program with the OpenSSL
    library.
    You must obey the GNU General Public License in all respects for all of
    the code used other than OpenSSL. If you modify file(s) with this
    exception, you may extend this exception to your version of the file(s),
    but you are not obligated to do so. If you do not wish to do so, delete
    this exception statement from your version. If you delete this exception
    statement from all source files in the program, then also delete it here.
*/


Ext.namespace('Deluge.plugins.tagging.util');


if (typeof(console) === 'undefined') {
  console = {
    log: function() {}
  };
}

if (typeof(Object.keys) === 'undefined') {
  Object.keys = function(obj) {
    var keys = [];

    for (var i in obj) {
      if (obj.hasOwnProperty(i)) {
        keys.push(i);
      }
    }

    return keys;
  };
}


Deluge.plugins.tagging.PLUGIN_NAME = 'Tagging';
Deluge.plugins.tagging.MODULE_NAME = 'tagging';
Deluge.plugins.tagging.DISPLAY_NAME = _('Tagging');

Deluge.plugins.tagging.STATUS_NAME =
  Deluge.plugins.tagging.MODULE_NAME + '_name';


Deluge.plugins.tagging.util.isReserved = function(id) {
  return (id == 'All' || id == 'None' || id == '');
};

Deluge.plugins.tagging.util.getParent = function(id) {
  return id.substring(0, id.lastIndexOf(':'));
};


Deluge.plugins.tagging.Plugin = Ext.extend(Deluge.Plugin, {

  name: Deluge.plugins.tagging.PLUGIN_NAME,

  onEnable: function() {
    this.registerTorrentStatus(Deluge.plugins.tagging.STATUS_NAME,
      Deluge.plugins.tagging.DISPLAY_NAME,
      {
        colCfg: {
          sortable: true
        }
      }
    );

    this.waitForClient(10);
  },

  onDisable: function() {
    if (this._rootMenu) {
      this._rootMenu.destroy();
      delete this._rootMenu;
    }

    this.deregisterTorrentStatus(Deluge.plugins.tagging.STATUS_NAME);

    console.log('%s disabled', Deluge.plugins.tagging.PLUGIN_NAME);
  },

  waitForClient: function(triesLeft) {
    if (triesLeft < 1) {
      console.log('%s RPC configuration timed out',
        Deluge.plugins.tagging.PLUGIN_NAME);
      return;
    }

    if (deluge.login.isVisible() || !deluge.client.core ||
        !deluge.client.tagging) {
      var self = this;
      var t = deluge.login.isVisible() ? triesLeft : triesLeft-1;
      setTimeout(function() { self.waitForClient.apply(self, [t]); }, 1000);
    } else {
      this._pollInit();
    }
  },

  _pollInit: function() {
    deluge.client.tagging.is_initialized({
      success: this._checkInit,
      scope: this
    });
  },

  _checkInit: function(result) {
    console.log('Waiting for %s core to be initialized...',
      Deluge.plugins.tagging.PLUGIN_NAME);

    if (result) {
      console.log('%s core is initialized',
        Deluge.plugins.tagging.PLUGIN_NAME);

      deluge.client.tagging.get_tag_updates_dict({
        success: this._finishInit,
        scope: this
      });
    } else {
      var self = this;
      setTimeout(function() { self._pollInit.apply(self); }, 3000);
    }
  },

  _finishInit: function(result) {
    if (result) {
      this._doUpdate(result);
      this._updateLoop();

      console.log('%s enabled', Deluge.plugins.tagging.PLUGIN_NAME);
    }
  },

  _doUpdate: function(result) {
    if (!result) {
      return;
    }

    this._lastUpdated = result.timestamp;

    if (this._rootMenu) {
      this._rootMenu.destroy();
      delete this._rootMenu;
    }

    var menu = new Ext.menu.Menu({ ignoreParentClicks: true });
    menu.add({
      text: _('Set Tag'),
      menu: this._createMenuFromData(result.data)
    });

    this._rootMenu = deluge.menus.torrent.add({
      text: Deluge.plugins.tagging.DISPLAY_NAME,
      menu: menu
    });
  },

  _updateLoop: function() {
    deluge.client.tagging.get_tag_updates_dict(this._lastUpdated, {
      success: function(result) {
        this._doUpdate(result);
        var self = this;
        setTimeout(function() { self._updateLoop.apply(self); }, 1000);
      },
      scope: this
    });
  },

  _getSortedKeys: function(data) {
    var sortedKeys = Object.keys(data).sort(function(a, b) {
      var aReserved = Deluge.plugins.tagging.util.isReserved(a);
      var bReserved = Deluge.plugins.tagging.util.isReserved(b);

      if (aReserved && bReserved) {
        return a < b ? -1 : a > b;
      } else if (aReserved) {
        return -1;
      } else if (bReserved) {
        return 1;
      }

      var aParent = Deluge.plugins.tagging.util.getParent(a);
      var bParent = Deluge.plugins.tagging.util.getParent(b);

      if (aParent == bParent) {
        return data[a].name < data[b].name ? -1 : data[a].name > data[b].name;
      } else {
        return aParent < bParent ? -1 : aParent > bParent;
      }
    });

    return sortedKeys;
  },

  _buildTagMap: function(sortedKeys, data) {
    var map = {};

    for (var i = 0; i < sortedKeys.length; i++) {
      var id = sortedKeys[i];
      var parent = Deluge.plugins.tagging.util.getParent(id);

      if (parent == '') {
        map[id] = [];
      } else if (parent in map) {
        map[parent].push(id);
        map[id] = [];
      }
    }

    return map;
  },

  _createMenuFromData: function(data) {
    var keys = this._getSortedKeys(data);
    var tagMap = this._buildTagMap(keys, data);

    var menu = new Ext.menu.Menu({ ignoreParentClicks: true });
    var submenus = {};

    menu.addMenuItem({
      text: _('None'),
      tag: 'None',
      handler: this._menuItemClicked,
      scope: this
    });
    menu.add({ xtype: 'menuseparator' });

    for (var i = 0; i < keys.length; i++) {
      var id = keys[i];

      if (Deluge.plugins.tagging.util.isReserved(id)) {
        continue;
      }

      if (id in tagMap) {
        var name = data[id].name;
        var submenu = false;

        if (tagMap[id].length > 0) {
          submenu = new Ext.menu.Menu({ ignoreParentClicks: true });
          submenu.addMenuItem({
            text: name,
            tag: id,
            handler: this._menuItemClicked,
            scope: this
          });
          submenu.add({ xtype: 'menuseparator' });

          submenus[id] = submenu;
        }

        var parent = Deluge.plugins.tagging.util.getParent(id);
        if (parent in submenus) {
          if (submenu) {
            submenus[parent].add({
              text: name,
              menu: submenu
            });
          } else {
            submenus[parent].addMenuItem({
              text: name,
              tag: id,
              handler: this._menuItemClicked,
              scope: this
            });
          }
        } else {
          if (submenu) {
            menu.add({
              text: name,
              menu: submenu
            });
          } else {
            menu.addMenuItem({
              text: name,
              tag: id,
              handler: this._menuItemClicked,
              scope: this
            });
          }
        }
      }
    }

    return menu;
  },

  _menuItemClicked: function(item, e) {
    var ids = deluge.torrents.getSelectedIds();

    deluge.client.tagging.set_torrent_tags(ids, item.tag, {
      success: function() {},
      scope: this
    });
  }
});

Deluge.registerPlugin(Deluge.plugins.tagging.PLUGIN_NAME,
  Deluge.plugins.tagging.Plugin);
