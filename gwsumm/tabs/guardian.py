# coding=utf-8
# Copyright (C) Duncan Macleod (2014)
#
# This file is part of GWSumm
#
# GWSumm is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# GWSumm is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with GWSumm.  If not, see <http://www.gnu.org/licenses/>

"""Definition of the `GuardianTab`
"""

from __future__ import print_function

import re

try:
    from collections import OrderedDict
except ImportError:
    from ordereddict import OrderedDict

from dateutil import tz

import numpy

from astropy.time import Time

from gwpy.segments import (DataQualityDict, SegmentList, Segment)
from gwpy.plotter import SegmentPlot

from ..config import (GWSummConfigParser, NoOptionError)
from ..state import ALLSTATE
from .registry import (get_tab, register_tab)
from .. import (globalv, version, html)
from ..data import (get_timeseries, get_timeseries_dict)
from ..segments import get_segments
from ..plot.registry import (get_plot, register_plot)
from ..utils import (vprint, re_quote)

__author__ = 'Duncan Macleod <duncan.macleod@ligo.org>'
__version__ = version.version

Tab = get_tab('default')
UTC = tz.gettz('UTC')
REQUESTSTUB = '+request'
NOMINALSTUB = '+nominal'
MODE_COLORS = ['grey', 'magenta', 'red', 'saddlebrown']


class GuardianTab(Tab):
    """Summarises the data recorded by an Advanced LIGO Guardian node.

    Each guardian node controls and monitors state transitions for a
    specific subsystem of the Advanced LIGO interferometer. The
    `GuardianTab` summarises those data with a state segments plot, a
    transitions summary table, and a detailed list of transitions and
    segments for each listed state.
    """
    type = 'archived-guardian'

    @classmethod
    def from_ini(cls, config, section, plotdir='plots', base=''):
        """Define a new `GuardianTab`.
        """
        new = super(Tab, cls).from_ini(
                  config, section, base=base)
        if len(new.states) > 1 or new.states[0].name != ALLSTATE:
            raise ValueError("GuardianTab does not accept state selection")
        new.plots = []
        new.plotdir = plotdir
        new.ifo = config.get(section, 'ifo')

        # record node and states
        new.node = config.get(section, 'node')
        new.grdstates = OrderedDict()
        plot = []
        for key, name in config.nditems(section):
            try:
                key = int(key)
            except ValueError:
                continue
            else:
                if name[0] == '*':
                    plot.append(False)
                else:
                    plot.append(True)
                new.grdstates[int(key)] = name.strip('*')
        try:
            new.transstates = map(
                int, config.get(section, 'transitions').split(','))
        except NoOptionError:
            new.transstates = new.grdstates.keys()

        # build plots
        new.segmenttag = '%s:%s %%s' % (new.ifo, new.node)
        labels = [l for i, l in enumerate(new.grdstates.values()[::-1]) if
                  plot[-i-1]]
        flags = [new.segmenttag % name for name in labels]
        new.plots.append(get_plot('guardian')(
            flags, new.span[0], new.span[1], labels=labels, outdir=plotdir,
            valid={'hatch': 'x', 'alpha': 0.1, 'facecolor': 'none'},
            tag='GRD_%s_SEGMENTS' % re.sub('[-\s]', '_', new.node),
            title='%s Guardian %s state' % (
                new.ifo, new.node.replace('_', r'\_')), zorder=2))
        return new

    def process(self, nds='guess', multiprocess=True,
                config=GWSummConfigParser(), datacache=None,
                **kwargs):
        """Process data for the given state.
        """
        ifo = self.ifo

        for p in self.plots:
            if p.outputfile in globalv.WRITTEN_PLOTS:
                p.new = False

        # --------------------------------------------------------------------
        # work out which channels are needed

        prefix = '%s:GRD-%s_%%s' % (self.ifo, self.node)

        state = sorted(self.states, key=lambda s: abs(s.active))[0]
        alldata = get_timeseries_dict(
            [prefix % x for x in ['STATE_N', 'REQUEST_N', 'NOMINAL_N',
                                  'MODE', 'OK']],
            state, config=config, nds=nds, multiprocess=multiprocess,
            cache=datacache, dtype='int16')
        grddata = alldata[prefix % 'STATE_N']
        reqdata = alldata[prefix % 'REQUEST_N']
        nomdata = alldata[prefix % 'NOMINAL_N']
        modedata = alldata[prefix % 'MODE']
        okdata = alldata[prefix % 'OK']
        vprint("    All time-series data loaded\n")

        # --------------------------------------------------------------------
        # find segments and transitions

        self.transitions = dict((v, []) for v in self.grdstates)

        for sdata, rdata, ndata, okdata in zip(
                grddata, reqdata, nomdata, okdata):
            ssegs = DataQualityDict()
            rsegs = DataQualityDict()
            nsegs = DataQualityDict()
            oksegs = (okdata == 1).to_dqflag(name='Node OK')
            for v, name in self.grdstates.iteritems():
                # get segments for state
                tag = self.segmenttag % name
                instate = sdata == v
                ssegs[tag] = instate.to_dqflag(name=name)
                transin = (numpy.diff(
                    instate.astype(int)) == 1).nonzero()[0] + 1
                transout = (numpy.diff(
                    instate.astype(int)) == -1).nonzero()[0] + 1
                for i, j in zip(transin, transout):
                    t = sdata.times[i]
                    from_ = sdata[i-1].value
                    to_ = sdata[j].value
                    self.transitions[v].append((t, from_, to_))
                # get segments for request
                tag = self.segmenttag % name + REQUESTSTUB
                instate = rdata == v
                rsegs[tag] = instate.to_dqflag(name=name)
                # get segments for nominal
                tag = self.segmenttag % name + NOMINALSTUB
                nom = ndata == v
                nsegs[tag] = nom.to_dqflag(name=name)

            globalv.SEGMENTS += ssegs
            globalv.SEGMENTS += rsegs
            globalv.SEGMENTS += nsegs
            globalv.SEGMENTS += {self.segmenttag % 'OK': oksegs}

        super(GuardianTab, self).process(
            config=config, nds=nds, multiprocess=multiprocess,
            datacache=datacache, **kwargs)

    def write_state_html(self, state):
        """Write the HTML for the given state of this `GuardianTab`
        """
        page = self.scaffold_plots()
        page.div(class_='alert alert-info alert-dismissible')
        # add close button
        page.button(type="button", class_="close", **{'data-dismiss': "alert"})
        page.span('&times;', **{'aria-hidden': "true"})
        page.span('Close', class_="sr-only")
        page.button.close()
        # show message
        page.p('For all of the following data, "Unknown" simply labels any '
               'state in this node that was not chosen for display, and does '
               'not mean that the state was unrecognised by the Guardian '
               'system. Transitions from an "Unkown" state are not listed in '
               'the below table, but are included in the totals.')
        page.div.close()

        page.div(class_='row')
        page.div(class_='col-md-12')

        # draw table
        page.h2('State transitions')
        page.p("The table below lists for each state (row), the number of "
               "transitions into that state from each other state (columns).")
        page.p("Only those states named in the configuration are shown, but "
               "the 'Total' includes transitions from any and all states. "
               "'-' indicates no transitions from that state")
        page.table(class_='transitions data')
        page.tr(class_='header')
        for th in ['State'] + self.grdstates.values() + ['Total']:
            page.th(th)
        page.tr.close()
        for i, bit in enumerate(self.transstates):
            page.tr()
            name = self.grdstates[bit].strip('*')
            page.th(name)
            for j, bit2 in enumerate(self.grdstates):
                if i == j:
                    page.td('-', class_='IOP')
                    continue
                count = len([t for t in self.transitions[bit] if
                             t[1] == bit2])
                if count:
                    page.td(str(count))
                else:
                    page.td('-')
            page.th(str(len(self.transitions[bit])))
            page.tr.close()
        page.table.close()
        page.div.close()
        page.div.close()

        # summarise each state
        page.div(class_='row')
        page.div(class_='col-md-12')
        page.h2('State details')
        page.div(class_='panel-group', id='accordion')
        for i, bit in enumerate(self.grdstates):
            name = self.grdstates[bit].strip('*')
            page.div(class_='panel well panel-primary', id=str(bit))
            # heading
            page.div(class_='panel-heading')
            page.a(href='#collapse%d' % bit,
                   **{'data-toggle': 'collapse', 'data-parent': '#accordion'})
            page.h4(name, class_='panel-title')
            page.a.close()
            page.div.close()

            # body
            page.div(id='collapse%d' % bit, class_='panel-collapse collapse')
            page.div(class_='panel-body')

            # print transitions
            page.p('This state was active %d times as follows:'
                   % len(self.transitions[bit]))
            headers = ['GPS time', 'UTC time', 'Local time',
                       'Transition from', 'Exited to']
            data = []
            if self.ifo in ['H1', 'C1', 'P1']:
                localzone = tz.gettz('America/Los_Angeles')
            elif self.ifo in ['L1']:
                localzone = tz.gettz('America/Chicago')
            else:
                localzone = tz.gettz('Europe/Berlin')
            for t, from_, to_ in self.transitions[bit]:
                t2 = Time(t, format='gps', scale='utc')
                tlocal = Time(
                    t2.datetime.replace(tzinfo=UTC).astimezone(localzone),
                    format='datetime', scale='utc')
                data.append((
                    t, t2.iso, tlocal.iso,
                    '%s [%d]' % (self.grdstates.get(from_, 'Unknown'), from_),
                    '%s [%d]' % (self.grdstates.get(to_, 'Unknown'), to_)))
            page.add(str(html.data_table(headers, data, table='guardian data')))

            # print segments
            flag = get_segments(self.segmenttag % name, state.active,
                                query=False).copy()
            livetime = abs(flag.active)
            try:
                duty = abs(flag.active) / float(abs(flag.valid)) * 100.
            except ZeroDivisionError:
                duty = 0
            page.p('This state was active for %.2f seconds (%.2f%%) during '
                   'the following segments:' % (livetime, duty))
            page.add(str(self.print_segments(flag)))
            page.div.close()
            page.div.close()
            page.div.close()
        page.div.close()
        page.div.close()
        page.div.close()

        return super(Tab, self).write_state_html(state, plots=False,
                                                     pre=page)
register_tab(GuardianTab)


class GuardianStatePlot(get_plot('segments')):
    type = 'guardian'
    defaults = get_plot('segments').defaults
    defaults.update({
        'color': None,
        'insetlabels': 'inset',
        'edgecolor': 'black',
        'linewidth': 0.5,
        'requestcolor': (0., .4, 1.),
        'nominalcolor': (1.0, 0.7, 0.0),
        'legend_loc': 'upper left',
        'legend_bbox_to_anchor': (1.01, 1),
        'legend_borderaxespad': 0.,
        'legend_fontsize': 12,
        'legend_title': 'Node state',
    })

    @property
    def node(self):
        return self.flags[0].split(' ', 1)[0][3:]

    @property
    def ifo(self):
        return list(self.ifos)[0]

    def process(self):
        (plot, axes) = self.init_plot(plot=SegmentPlot)
        ax = axes[0]

        # get labels
        flags = map(lambda f: str(f).replace('_', r'\_'), self.flags)
        labels = self.pargs.pop('labels', self.pargs.pop('label', flags))
        ax.set_insetlabels(self.pargs.pop('insetlabels', True))
        if isinstance(labels, (unicode, str)):
            labels = labels.split(',')
        labels = map(lambda s: re_quote.sub('', str(s).strip('\n ')), labels)

        # parse plotting arguments
        legendargs = self.parse_legend_kwargs()
        activecolor, validcolor = self.get_segment_color()
        nominalcolor = self.pargs.pop('nominalcolor')
        requestcolor = self.pargs.pop('requestcolor')
        plotargs = self.parse_plot_kwargs()[0]
        plotargs.pop('label')
        actargs = plotargs.copy()
        plotargs.update({
            'facecolor': nominalcolor,
            'edgecolor': 'none',
        })
        reqargs = plotargs.copy()
        reqargs.update({
            'facecolor': requestcolor,
            'valid': None,
            })
        actargs.update({
            'facecolor': activecolor,
            'valid': None,
        })

        if self.state and not self.all_data:
            valid = self.state.active
        else:
            valid = SegmentList([self.span])

        # plot segments
        for y, (flag, label) in enumerate(zip(self.flags, labels)[::-1]):
            inreq = str(flag) + REQUESTSTUB
            nominal = str(flag) + NOMINALSTUB
            segs = get_segments([flag, inreq, nominal], validity=valid,
                                query=False)
            ax.plot(segs[nominal], label=label, y=y, height=1., **plotargs)
            ax.plot(segs[inreq], label=None, y=y, collection=False, **reqargs)
            ax.plot(segs[flag], label=None, y=y, collection=False,
                    height=.6, **actargs)

        # make custom legend
        seg = SegmentList([Segment(self.start - 10, self.start - 9)])
        v = plotargs.pop('valid', None)
        if v:
            v.pop('collection', None)
            v = ax.build_segment(seg, 0, **v)
        n = ax.build_segment(seg, 0, facecolor=nominalcolor,
                             edgecolor=plotargs['edgecolor'])
        a = ax.build_segment(seg, 0, facecolor=requestcolor,
                             edgecolor=plotargs['edgecolor'])
        b = ax.build_segment(seg, 0, facecolor=activecolor,
                             edgecolor=actargs['edgecolor'])
        if v:
            ax.legend([v, n, a, b], ['Alive', 'Nominal', 'Request', 'Active'],
                      **legendargs)
        else:
            ax.legend([n, a, b], ['Nominal', 'Request', 'Active'], **legendargs)

        # customise plot
        for key, val in self.pargs.iteritems():
            try:
                getattr(ax, 'set_%s' % key)(val)
            except AttributeError:
                setattr(ax, key, val)
        if 'ylim' not in self.pargs:
            ax.set_ylim(-0.5, len(self.flags) - 0.5)

        # fake colorbar
        if not plot.colorbars:
            plot.add_colorbar(ax=ax, visible=False)

        # add node MODE along the bottom
        ylim = ax.get_ylim()
        sax = None
        legentry = OrderedDict()
        try:
            grdmode = get_timeseries(
               '%s:GRD-%s_MODE' % (self.ifo, self.node),
               valid, query=False).join(gap='pad', pad=-1)
        except KeyError:
            pass
        else:
            nodemode = OrderedDict()
            try:
                from guardian.daemon import DAEMON_MODES
            except ImportError:
                DAEMON_MODES = ('STOP', 'PAUSE', 'EXEC', 'MANAGED')
            # get modes
            modes = OrderedDict()
            for i, (m, c) in enumerate(zip(DAEMON_MODES, MODE_COLORS)):
                modes[m] = (grdmode == i).to_dqflag()
                if sax is None:
                    sax = plot.add_state_segments(
                        modes[m].active,
                        plotargs={'y': 0, 'facecolor': c, 'edgecolor': 'none',
                                  'collection': False})
                else:
                    sax.plot_segmentlist(modes[m].active, facecolor=c, y=0,
                                         edgecolor='none', collection=False)
                legentry[m.title()] = ax.build_segment(seg, 0, facecolor=c,
                                                       edgecolor='none')

        # add OK segments along the bottom
        try:
            ok = get_segments(flag.split(' ', 1)[0] + ' OK', validity=valid,
                              query=False)
        except NameError:
            pass
        else:
            # just plot OK
            if sax is not None:
                sax.plot_segmentlist(ok.active, facecolor=activecolor, y=0,
                                     edgecolor=actargs['edgecolor'],
                                     collection=False, label='Node `OK\'')
                # add gap in legend
                legentry['$--$'] = ax.build_segment(
                    seg, 0, facecolor='w', fill=False, edgecolor='none',
                    linewidth=0)
                # add OK to legend
                legentry['Node `OK\''] = ax.build_segment(
                    seg, 0, facecolor=activecolor,
                    edgecolor=actargs['edgecolor'])
            else:
                sax = plot.add_state_segments(ok, ax, plotargs={
                    'label': 'Node `OK\'', 'y': 0, 'facecolor': activecolor,
                    'known': {'facecolor': 'red', 'edgecolor': 'black'}})

        # make custom legend
        if sax is not None:
            if legentry:
                sax.legend(legentry.values(), legentry.keys(), loc='lower left',
                           bbox_to_anchor=(1.01, 0.), borderaxespad=0,
                           fontsize=12, title='Node mode')
            sax.tick_params(axis='y', which='major', labelsize=12)
            sax.set_epoch(float(self.pargs.get('epoch', self.start)))

        return self.finalize()

register_plot(GuardianStatePlot)
