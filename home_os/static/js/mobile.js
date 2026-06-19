(function() {
    'use strict';

    const isStandalone = window.matchMedia('(display-mode: standalone)').matches
        || window.navigator.standalone === true;
    const isMobile = window.matchMedia('(max-width: 767px)').matches;

    if (!isStandalone && !isMobile) return;

    document.documentElement.classList.add('ios-standalone');

    const HomeOS = window.HomeOS = window.HomeOS || {};
    HomeOS.mobile = {};

    // ─── Large Title Scroll Handler ───

    HomeOS.mobile.initLargeTitle = function() {
        const header = document.querySelector('.ios-page-header');
        if (!header) return;

        const largeTitle = header.querySelector('.ios-large-title');
        const navBar = header.querySelector('.ios-nav-bar');
        const navTitle = header.querySelector('.ios-nav-title');
        const scrollContainer = document.querySelector('.main-content');
        if (!largeTitle || !navBar || !scrollContainer) return;

        const COLLAPSE_DISTANCE = 52;
        let ticking = false;

        function onScroll() {
            if (ticking) return;
            ticking = true;
            requestAnimationFrame(function() {
                const scrollY = scrollContainer.scrollTop;
                const progress = Math.min(scrollY / COLLAPSE_DISTANCE, 1);

                // Large title fades and shrinks
                largeTitle.style.opacity = 1 - progress;
                largeTitle.style.transform = 'translateY(' + (-progress * 10) + 'px)';

                // Nav bar background fades in
                navBar.style.setProperty('--nav-opacity', progress);

                // Toggle scrolled class for header background
                if (scrollY > 2) {
                    header.classList.add('scrolled');
                } else {
                    header.classList.remove('scrolled');
                }

                // Inline title fades in during last 30%
                const titleProgress = Math.max((progress - 0.7) / 0.3, 0);
                if (navTitle) {
                    navTitle.style.opacity = titleProgress;
                }

                ticking = false;
            });
        }

        scrollContainer.addEventListener('scroll', onScroll, { passive: true });
        onScroll();
    };

    // ─── Gesture Engine ───

    HomeOS.mobile.gestures = {
        _swipeEdgeWidth: 20,
        _longPressDuration: 500,
        _swipeVelocityThreshold: 300,
        _swipeDistanceThreshold: 0.4,

        init: function() {
            this._initSwipeBack();
            this._initLongPress();
        },

        // ─── Swipe-Back ───

        _initSwipeBack: function() {
            var self = this;
            var tracking = false;
            var startX = 0;
            var startY = 0;
            var startTime = 0;
            var currentX = 0;
            var shadow = document.createElement('div');
            shadow.className = 'ios-swipe-shadow';
            document.body.appendChild(shadow);

            document.addEventListener('pointerdown', function(e) {
                if (e.clientX > self._swipeEdgeWidth) return;
                if (!self._canSwipeBack()) return;

                tracking = true;
                startX = e.clientX;
                startY = e.clientY;
                startTime = Date.now();
                currentX = 0;
                e.preventDefault();
            }, { passive: false });

            document.addEventListener('pointermove', function(e) {
                if (!tracking) return;

                var dx = e.clientX - startX;
                var dy = Math.abs(e.clientY - startY);

                // Cancel if vertical scroll dominates
                if (dy > Math.abs(dx) && dx < 20) {
                    tracking = false;
                    shadow.classList.remove('active');
                    return;
                }

                if (dx < 0) dx = 0;
                currentX = dx;

                var progress = dx / window.innerWidth;
                shadow.classList.add('active');
                shadow.style.opacity = Math.min(progress * 3, 1);
            });

            document.addEventListener('pointerup', function() {
                if (!tracking) return;
                tracking = false;
                shadow.classList.remove('active');
                shadow.style.opacity = '';

                var elapsed = Date.now() - startTime;
                var velocity = (currentX / elapsed) * 1000;
                var distanceRatio = currentX / window.innerWidth;

                if (velocity > self._swipeVelocityThreshold || distanceRatio > self._swipeDistanceThreshold) {
                    window.history.back();
                }
            });
        },

        _canSwipeBack: function() {
            // Don't swipe back if at root (no history) or if a horizontal scroller is active
            if (window.history.length <= 1) return false;

            var scrollers = document.querySelectorAll('[style*="overflow-x"], .finder-icon-grid, .finder-grid');
            for (var i = 0; i < scrollers.length; i++) {
                if (scrollers[i].scrollLeft > 0) return false;
            }
            return true;
        },

        // ─── Long-Press ───

        _initLongPress: function() {
            var self = this;
            var timer = null;
            var target = null;
            var startX = 0;
            var startY = 0;

            document.addEventListener('pointerdown', function(e) {
                var el = e.target.closest('[data-longpress], .finder-icon-item, .finder-icon-grid-item, .finder-list-row, .ios-tab-item[data-quick-actions]');
                if (!el) return;

                target = el;
                startX = e.clientX;
                startY = e.clientY;

                timer = setTimeout(function() {
                    // Visual feedback
                    target.classList.add('ios-longpress-active');

                    // Fire custom event
                    var event = new CustomEvent('ios-longpress', {
                        bubbles: true,
                        detail: { target: target, x: startX, y: startY }
                    });
                    target.dispatchEvent(event);

                    // Remove visual feedback after menu shows
                    setTimeout(function() {
                        if (target) target.classList.remove('ios-longpress-active');
                    }, 200);
                }, self._longPressDuration);
            });

            document.addEventListener('pointermove', function(e) {
                if (!timer) return;
                var dx = Math.abs(e.clientX - startX);
                var dy = Math.abs(e.clientY - startY);
                if (dx > 10 || dy > 10) {
                    clearTimeout(timer);
                    timer = null;
                    if (target) target.classList.remove('ios-longpress-active');
                }
            });

            document.addEventListener('pointerup', function() {
                if (timer) {
                    clearTimeout(timer);
                    timer = null;
                }
            });

            document.addEventListener('pointercancel', function() {
                if (timer) {
                    clearTimeout(timer);
                    timer = null;
                }
                if (target) target.classList.remove('ios-longpress-active');
            });
        }
    };

    // ─── Context Menu ───

    HomeOS.mobile.contextMenu = {
        _backdrop: null,
        _menu: null,

        show: function(items, x, y, options) {
            options = options || {};
            this.dismiss();

            // Create backdrop
            this._backdrop = document.createElement('div');
            this._backdrop.className = 'ios-context-backdrop';
            this._backdrop.addEventListener('click', this.dismiss.bind(this));

            // Create menu
            this._menu = document.createElement('div');
            this._menu.className = 'ios-context-menu' + (options.isTabMenu ? ' tab-menu' : '');

            var html = '';
            for (var i = 0; i < items.length; i++) {
                var item = items[i];
                if (item.separator) {
                    html += '<div class="ios-context-menu-sep"></div>';
                    continue;
                }
                var cls = 'ios-context-menu-item' + (item.destructive ? ' destructive' : '');
                html += '<div class="' + cls + '" data-action="' + (item.action || '') + '">';
                html += '<span>' + item.label + '</span>';
                if (item.icon) {
                    html += '<span class="ios-context-menu-item-icon">' + item.icon + '</span>';
                }
                html += '</div>';
            }
            this._menu.innerHTML = html;

            document.body.appendChild(this._backdrop);
            document.body.appendChild(this._menu);

            // Position
            if (options.isTabMenu) {
                var tabRect = options.anchorEl.getBoundingClientRect();
                this._menu.style.left = (tabRect.left + tabRect.width / 2 - this._menu.offsetWidth / 2) + 'px';
                this._menu.style.setProperty('--notch-left', '50%');
            } else {
                var menuW = this._menu.offsetWidth;
                var menuH = this._menu.offsetHeight;
                var posX = Math.min(x, window.innerWidth - menuW - 16);
                var posY = Math.min(y, window.innerHeight - menuH - 16);
                posX = Math.max(16, posX);
                posY = Math.max(16, posY);
                this._menu.style.left = posX + 'px';
                this._menu.style.top = posY + 'px';
            }

            // Animate in
            var self = this;
            requestAnimationFrame(function() {
                self._backdrop.classList.add('active');
                self._menu.classList.add('visible');
            });

            // Handle item clicks
            this._menu.addEventListener('click', function(e) {
                var menuItem = e.target.closest('.ios-context-menu-item');
                if (!menuItem) return;

                var action = menuItem.getAttribute('data-action');
                self.dismiss();

                if (options.onAction) {
                    options.onAction(action);
                }
            });
        },

        dismiss: function() {
            if (this._menu) {
                this._menu.classList.remove('visible');
            }
            if (this._backdrop) {
                this._backdrop.classList.remove('active');
            }

            var menu = this._menu;
            var backdrop = this._backdrop;
            this._menu = null;
            this._backdrop = null;

            setTimeout(function() {
                if (menu && menu.parentNode) menu.parentNode.removeChild(menu);
                if (backdrop && backdrop.parentNode) backdrop.parentNode.removeChild(backdrop);
            }, 200);
        }
    };

    // ─── Bottom Sheet Controller ───

    HomeOS.mobile.sheet = {
        _backdrop: null,
        _sheet: null,
        _startY: 0,
        _currentY: 0,
        _isDragging: false,

        show: function(contentHtml, options) {
            options = options || {};
            var detent = options.detent || 'full'; // 'half' or 'full'

            // Create backdrop
            this._backdrop = document.createElement('div');
            this._backdrop.className = 'ios-sheet-backdrop';
            this._backdrop.addEventListener('click', this.dismiss.bind(this));

            // Create sheet
            this._sheet = document.createElement('div');
            this._sheet.className = 'ios-sheet' + (detent === 'half' ? ' half' : '');
            this._sheet.innerHTML =
                '<div class="ios-sheet-grabber"></div>' +
                '<div class="ios-sheet-content">' + contentHtml + '</div>';

            document.body.appendChild(this._backdrop);
            document.body.appendChild(this._sheet);

            // Trigger animation on next frame
            var self = this;
            requestAnimationFrame(function() {
                self._backdrop.classList.add('active');
                self._sheet.classList.add('visible');
                if (detent === 'full') {
                    document.querySelector('.app-layout').classList.add('sheet-presenting', 'pushed');
                }
            });

            // Setup drag-to-dismiss
            this._setupDrag();
        },

        dismiss: function() {
            var self = this;
            if (!this._sheet) return;

            this._sheet.classList.remove('visible');
            this._backdrop.classList.remove('active');
            document.querySelector('.app-layout').classList.remove('pushed');

            setTimeout(function() {
                document.querySelector('.app-layout').classList.remove('sheet-presenting');
                if (self._backdrop && self._backdrop.parentNode) {
                    self._backdrop.parentNode.removeChild(self._backdrop);
                }
                if (self._sheet && self._sheet.parentNode) {
                    self._sheet.parentNode.removeChild(self._sheet);
                }
                self._backdrop = null;
                self._sheet = null;
            }, 500);
        },

        _setupDrag: function() {
            var self = this;
            var grabber = this._sheet.querySelector('.ios-sheet-grabber');
            var sheet = this._sheet;

            var onPointerDown = function(e) {
                self._isDragging = true;
                self._startY = e.clientY;
                self._currentY = 0;
                sheet.style.transition = 'none';
                document.addEventListener('pointermove', onPointerMove);
                document.addEventListener('pointerup', onPointerUp);
            };

            var onPointerMove = function(e) {
                if (!self._isDragging) return;
                var dy = e.clientY - self._startY;
                if (dy < 0) dy = 0; // Only allow drag down
                self._currentY = dy;
                sheet.style.transform = 'translateY(' + dy + 'px)';
            };

            var onPointerUp = function() {
                self._isDragging = false;
                document.removeEventListener('pointermove', onPointerMove);
                document.removeEventListener('pointerup', onPointerUp);
                sheet.style.transition = '';

                var sheetHeight = sheet.offsetHeight;
                if (self._currentY > sheetHeight * 0.5) {
                    self.dismiss();
                } else {
                    sheet.style.transform = '';
                    sheet.classList.add('visible');
                }
            };

            grabber.addEventListener('pointerdown', onPointerDown);
            sheet.addEventListener('pointerdown', function(e) {
                if (e.target === sheet || e.target === grabber) {
                    onPointerDown(e);
                }
            });
        }
    };

    // ─── Init on DOM ready ───

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    function init() {
        HomeOS.mobile.gestures.init();
        initContextMenuListeners();
    }

    function injectPageHeader() {
        var main = document.querySelector('.main-content');
        if (!main) return;
        if (main.querySelector('.ios-page-header')) return;

        // Find first h1 in main content
        var h1 = main.querySelector('h1');
        var title = h1 ? h1.textContent.trim() : document.title.replace(' — Home OS', '');

        // Create page header
        var header = document.createElement('div');
        header.className = 'ios-page-header';
        header.innerHTML =
            '<div class="ios-nav-bar">' +
                '<span class="ios-nav-title">' + title + '</span>' +
            '</div>' +
            '<div class="ios-large-title">' + title + '</div>';

        // Insert at top of main content
        main.insertBefore(header, main.firstChild);

        // Hide original h1
        if (h1) h1.style.display = 'none';
    }

    function initContextMenuListeners() {
        // File/folder context menu
        document.addEventListener('ios-longpress', function(e) {
            var target = e.detail.target;

            // Tab bar quick actions
            if (target.classList.contains('ios-tab-item') && target.dataset.quickActions) {
                var actions = JSON.parse(target.dataset.quickActions);
                var items = actions.map(function(a) {
                    return { label: a.label, action: a.action };
                });
                HomeOS.mobile.contextMenu.show(items, e.detail.x, e.detail.y, {
                    isTabMenu: true,
                    anchorEl: target,
                    onAction: function(action) {
                        handleQuickAction(action);
                    }
                });
                return;
            }

            // File grid item context menu
            if (target.classList.contains('finder-icon-item') || target.classList.contains('finder-icon-grid-item') || target.classList.contains('finder-list-row')) {
                var fileName = target.querySelector('.finder-icon-name, .finder-icon-grid-name, .flist-name');
                var name = fileName ? fileName.textContent.trim() : 'item';
                var isDir = target.dataset.type === 'dir' || target.querySelector('.finder-icon-grid-icon svg[data-folder]');

                var items = [
                    { label: 'Open', action: 'open', icon: '<svg fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M13.5 6H5.25A2.25 2.25 0 003 8.25v10.5A2.25 2.25 0 005.25 21h10.5A2.25 2.25 0 0018 18.75V10.5m-10.5 6L21 3m0 0h-5.25M21 3v5.25"/></svg>' },
                    { label: 'Download', action: 'download', icon: '<svg fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5M16.5 12L12 16.5m0 0L7.5 12m4.5 4.5V3"/></svg>' },
                    { separator: true },
                    { label: 'Copy', action: 'copy', icon: '<svg fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M15.666 3.888A2.25 2.25 0 0013.5 2.25h-3c-1.03 0-1.9.693-2.166 1.638m7.332 0c.055.194.084.4.084.612v0a.75.75 0 01-.75.75H9.75a.75.75 0 01-.75-.75v0c0-.212.03-.418.084-.612m7.332 0c.646.049 1.288.11 1.927.184 1.1.128 1.907 1.077 1.907 2.185V19.5a2.25 2.25 0 01-2.25 2.25H6.75A2.25 2.25 0 014.5 19.5V6.257c0-1.108.806-2.057 1.907-2.185a48.208 48.208 0 011.927-.184"/></svg>' },
                    { label: 'Cut', action: 'cut', icon: '<svg fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M7.848 8.25l1.536.887M7.848 8.25a3 3 0 11-5.196-3 3 3 0 015.196 3zm9.304 0l-1.536.887M17.152 8.25a3 3 0 105.196-3 3 3 0 00-5.196 3zM12 17.25l-4.152-7.313M12 17.25l4.152-7.313M12 17.25V21m0-3.75l-4.152-7.313M12 17.25l4.152-7.313"/></svg>' },
                    { label: 'Rename', action: 'rename', icon: '<svg fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M16.862 4.487l1.687-1.688a1.875 1.875 0 112.652 2.652L10.582 16.07a4.5 4.5 0 01-1.897 1.13L6 18l.8-2.685a4.5 4.5 0 011.13-1.897l8.932-8.931z"/></svg>' },
                    { separator: true },
                    { label: 'Move to Trash', action: 'delete', destructive: true, icon: '<svg fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M14.74 9l-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 01-2.244 2.077H8.084a2.25 2.25 0 01-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 00-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 013.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 00-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 00-7.5 0"/></svg>' }
                ];

                HomeOS.mobile.contextMenu.show(items, e.detail.x, e.detail.y, {
                    onAction: function(action) {
                        handleFileAction(action, target, name);
                    }
                });
                return;
            }
        });
    }

    function handleQuickAction(action) {
        switch (action) {
            case 'newFolder':
                // Trigger the existing new folder modal/prompt
                var newFolderBtn = document.querySelector('[onclick*="newFolder"], [data-action="new-folder"]');
                if (newFolderBtn) newFolderBtn.click();
                break;
            case 'newChat':
                window.location.href = '/ai/chat';
                break;
            case 'newEvent':
                // Trigger calendar new event if available
                var newEventBtn = document.querySelector('[data-action="new-event"]');
                if (newEventBtn) newEventBtn.click();
                break;
            case 'recentFiles':
                window.location.href = '/files/search';
                break;
            case 'storage':
                window.location.href = '/settings';
                break;
        }
    }

    function handleFileAction(action, targetEl, fileName) {
        // Delegate to existing file action handlers if they exist
        switch (action) {
            case 'open':
                if (targetEl.href) window.location.href = targetEl.href;
                else if (targetEl.closest('a')) window.location.href = targetEl.closest('a').href;
                break;
            case 'download':
                var dlLink = targetEl.getAttribute('data-download') || targetEl.querySelector('[data-download]');
                if (dlLink) window.location.href = dlLink;
                else if (targetEl.href) window.location.href = targetEl.href + '?download=1';
                break;
            case 'rename':
                var renameBtn = targetEl.querySelector('[data-action="rename"]');
                if (renameBtn) renameBtn.click();
                else if (window.renameItem) window.renameItem(targetEl);
                break;
            case 'move':
                var moveBtn = targetEl.querySelector('[data-action="move"]');
                if (moveBtn) moveBtn.click();
                else if (window.moveItems) window.moveItems([targetEl]);
                break;
            case 'copy':
                var copyBtn = targetEl.querySelector('[data-action="copy"]');
                if (copyBtn) copyBtn.click();
                else if (window.copyItems) window.copyItems([targetEl]);
                break;
            case 'cut':
                var cutBtn = targetEl.querySelector('[data-action="cut"]');
                if (cutBtn) cutBtn.click();
                else if (window.cutItems) window.cutItems([targetEl]);
                break;
            case 'share':
                if (navigator.share) {
                    navigator.share({ title: fileName, text: 'Shared from Home OS' });
                }
                break;
            case 'delete':
                var deleteBtn = targetEl.querySelector('[data-action="delete"]');
                if (deleteBtn) deleteBtn.click();
                else if (window.deleteItems) window.deleteItems([targetEl]);
                break;
        }
    }
})();
