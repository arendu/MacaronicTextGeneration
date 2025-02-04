# -*- coding: utf-8 -*-
__author__ = 'arenduchintala'
from optparse import OptionParser
import itertools as it
import codecs
import numpy as np
import utils
import pdb,sys
from math import log, exp
import kenlm as lm
from editdistance import EditDistance as ED
from utils import get_meteor_score as meteor

from nltk import Tree
from nltk.draw.util import CanvasFrame
from nltk.draw import TreeWidget

global all_nonterminals, substring_translations, stopwords, lm_model, weight_ed, weight_binary_nt
global constituent_spans, weight_similarity, similarity_metric, weight_outside_similarity, ed, inclm
weight_outside_similarity = 1.0
similarity_metric = "e"
weight_similarity = 1.0
constituent_spans = {}
weight_binary_nt = 1.0
weight_ed = 1.0
weight_mt = 1.0
lm_tm_tension = 0.1
hard_prune = 10
stopwords = []
all_nonterminals = {}
substring_translations = {}


class NonTerminal(object):
    def __init__(self, idx, i, k):
        self.idx = idx
        self.span = (i, k)
        self.score = 0.0
        self.phrase = None
        self.german_phrase = None
        self._children = []

        self.isChildTerminal = False
        self.display_width = 0
        self.dropped = set([])
        self.inserted = set([])

    def add_inserted(self, ins):
        self.inserted.update(ins)

    def add_dropped(self, drop):
        self.dropped.update(drop)

    def add_terminalChild(self, nt_child):
        self._children = [nt_child.idx]

    def get_children(self):
        return self._children

    def add_nonTerminalChild(self, nt_idx):
        self._children.append(nt_idx)
        if len(self._children) > 2:
            raise BaseException("Binary or Unary Nodes only")
        else:
            pass

    def __str__(self):
        return ' '.join([str(self.idx), self.phrase.encode('utf-8'), str(self.score)])

    def __cmp__(self, other):
        if self.score < other.score:
            return -1
        elif self.score == other.score:
            return 0
        else:
            return 1


    def get_bracketed_repr(self, all_nonterminals):
        if len(self._children) == 0:
            return ' '.join(['(', self.phrase.encode('utf-8').replace(' ', '_'),
                             self.german_phrase.encode('utf-8').replace(' ', '_'), ')'])
        elif len(self._children) == 1:
            c1 = all_nonterminals[self._children[0]]
            return ' '.join(
                ['(', self.phrase.encode('utf-8').replace(' ', '_'), c1.get_bracketed_repr(all_nonterminals), ')'])
        elif len(self._children) == 2:
            c1 = all_nonterminals[self._children[0]]
            c2 = all_nonterminals[self._children[1]]
            return ' '.join(['(', self.phrase.encode('utf-8').replace(' ', '_'), c1.get_bracketed_repr(
                all_nonterminals), c2.get_bracketed_repr(all_nonterminals), ')'])


def logit(msg):
    sys.stderr.write(msg)
    sys.stderr.flush()

def read_substring_translations(substring_trans_file, substring_spans_file, inclm):
    global lm_tm_tension
    spans_by_line_num = {}
    line_num_2_sent_len = {}
    for idx, l in enumerate(open(substring_spans_file).readlines()):
        assert len(l.split()) == 4
        ls = l.split()
        spans_by_line_num[idx] = tuple([int(i) for i in ls[:-1]])
        line_num_2_sent_len[idx] = int(ls[-1])
    
    trans_by_span = {}
    for l in codecs.open(substring_trans_file, 'r', 'utf-8').readlines():
        parts = l.split('|||')
        trans = ' '.join(parts[1].split()[1:-1])
        line_num = int(parts[0])

        span_num = spans_by_line_num[line_num]
        (sent_num, st_span, en_span) = span_num
        sent_len = line_num_2_sent_len[line_num]

        only_tm_score = sum([float(s) for s in parts[-2].split()[-4:]])/4.0 
        full_mt_score = float(parts[-1].strip())
        height_f = (1.0 + float(en_span - st_span))/float(sent_len)
        #height_f = 0.5 + (height_f * 0.5)
        final_score_spl = height_f * full_mt_score + (1.0 - height_f) * only_tm_score

        final_score = full_mt_score 
        trans_for_line = trans_by_span.get(span_num, [])
        if inclm:
            trans_for_line.append((final_score_spl, trans))
        else:
            trans_for_line.append((final_score, trans))
        trans_by_span[span_num] = trans_for_line
    return trans_by_span


def corpus_spans(nbest):
    span_dict = {}
    for line in open(nbest, 'r').readlines():
        sid, translation = line.split('|||')[:2]
        # print translation
        parts = translation.strip().split('|')
        # print parts
        for part_id in xrange(0, len(parts) - 1, 2):
            # print parts[part_id], '<--', parts[part_id + 1]
            span = tuple(int(s) for s in [sid.strip()] + parts[part_id + 1].split('-'))
            # print span
            s = span_dict.get(span, set([]))
            s.add(parts[part_id].strip())
            span_dict[span] = s
    return span_dict


def get_similarity(t_x, E_y):
    global weight_ed, weight_binary_nt, similarity_metric, ed
    """
    :param t_x: a translation candidate for de substring g_x (cast as a unary nonterminal)
    :param E_y: a list of binary nonterminals which will become the child of current unary node
    :return: updated t_x with  similary score
    """
    inserted = None
    dropped = None
    max_ed_score = float('-inf')
    max_e_yt = None
    for e_y in E_y:
        # TODO: is editdistance_prob doing the right thing? insert/delete/substitute cost = 1/3
        # ed_score = np.log(edscore(t_x.phrase, e_y.phrase))
        if similarity_metric == "e":
            sim_score = (weight_ed * ed.editdistance_prob(t_x.phrase, e_y.phrase)) + (weight_binary_nt * e_y.score)
        elif similarity_metric == "c":
            cs_score, alignment = ed.editdistance(t_x.phrase.split(), e_y.phrase.split())
            cs_score  = 1.0 if cs_score > 1.0 else cs_score
            cs_score = 0.0 if cs_score < 0.0 else cs_score
            inserted,dropped = ed.alignmentdistance(t_x.phrase.split(), e_y.phrase.split())

            sim_score = (weight_ed * np.log(cs_score)) + (weight_binary_nt * e_y.score)
        elif similarity_metric == "m":
            sim_score = (weight_similarity * meteor(t_x.phrase.split(), e_y.phrase.split())) + (weight_binary_nt * e_y.score)
        # TODO:(+e_y.score) term not suppose to be?
        if sim_score > max_ed_score:
            max_ed_score = sim_score
            max_e_yt = e_y
        else:
            pass
    # TODO: this should not just be a simple log addition
    # TODO: must learn weights for this
    try:
        t_x.score = max_ed_score + t_x.score
    except ValueError:
        pdb.set_trace()
    if dropped is not None:
        t_x.add_dropped(dropped)
    if inserted is not None:
        t_x.add_inserted(inserted)
    t_x.add_nonTerminalChild(max_e_yt.idx)
    t_x.display_width = max_e_yt.display_width
    return t_x


def insert_stopword(e1, e2):
    """
    :param e1: non terminal
    :param e2: non terminal
    :return: score,phrase
    """
    global stopwords, lm_model
    l = []
    e = e1.phrase + ' ' + e2.phrase
    e_score = e1.score + e2.score  # TODO: figure out how to properly combine these scores
    no_sw = e_score + lm_model.score(e)  # TODO: how to combine lm_score with mt_scores
    l.append((no_sw, e))
    for sw in stopwords:
        e_ws = e1.phrase + ' ' + sw + ' ' + e2.phrase
        sw = e_score + lm_model.score(e_ws)  # TODO: how to combine lm_score with mt_scores
        l.append((sw, e_ws))
    return sorted(l, reverse=True)[0]


def get_combinations(E_y, E_z, g_x):
    """
    :param E_y: weighted set of translations of left child
    :param E_z: weighted set of translations of right child
    :return: weighted set of translations for current node
    """
    global all_nonterminals
    nonterminals = []
    ss = {}
    for e1, e2 in it.chain(it.product(E_y, E_z), it.product(E_z, E_y)):
        insert_lm_score, e_phrase = insert_stopword(e1,
                                                    e2)  # returns best (score,phrase) with stop word insertion LM cost
        e_score = e1.score + e2.score
        i = min(e1.span[0], e2.span[0])
        k = max(e2.span[1], e2.span[1])
        # TODO: score e_x also based on how good a translation of g_x is it, how to do this
        # TODO: what if the phrase e_phrase does not exist in top n translations of g_x?
        current_score = ss.get(e_phrase, float('-inf'))
        if e_score > current_score:
            ss[e_phrase] = e_score
            nt = NonTerminal(len(all_nonterminals), i, k)
            nt.score = e_score
            nt.phrase = e_phrase
            nt.display_width = e1.display_width + e2.display_width + 1
            nt.add_nonTerminalChild(e1.idx)
            nt.add_nonTerminalChild(e2.idx)
            nonterminals.append(nt)
            all_nonterminals[nt.idx] = nt

    return sorted(nonterminals, reverse=True)


def get_single_word_translations(g_x, sent_number, idx):
    global all_nonterminals, hard_prune
    nonterminals = []
    ss = sorted(substring_translations[sent_number, idx, idx], reverse=True)[:hard_prune]

    for score, phrase in ss:
        """
        lowest level english nonterminal
        """
        nt = NonTerminal(len(all_nonterminals), idx, idx)
        all_nonterminals[nt.idx] = nt
        nt.score = score
        nt.phrase = phrase
        nt.german_phrase = g_x
        nonterminals.append(nt)
        nt.display_width = max(len(g_x.encode('utf-8')) + 2, len(phrase.encode('utf-8')) + 2)
        nt.isChildTerminal = True
    return nonterminals


def get_human_reference(ref):
    global all_nonterminals
    nt = NonTerminal(len(all_nonterminals), 0, n - 1)
    nt.score = 0.0
    nt.phrase = ref
    all_nonterminals[nt.idx] = nt
    return [nt]


def get_substring_translations(sent_number, i, k):
    global substring_translations, all_nonterminals, hard_prune, weight_mt
    ss = sorted(substring_translations[sent_number, i, k], reverse=True)[:hard_prune]
    nonterminals = []
    for score, phrase in ss:
        nt = NonTerminal(len(all_nonterminals), i, k)
        nt.score = (score * weight_mt)
        nt.phrase = phrase
        nonterminals.append(nt)
        all_nonterminals[nt.idx] = nt
    return nonterminals


def display_best_nt(node, i, k):
    """
    :param node: a node is merely a list of non-terminals
    :return:
    """
    print '***************************************************'
    print 'node span', i, k, 'best nonterminal'
    b_score, b_nt = sorted([(nt.score, nt) for nt in node], reverse=True)[0]
    display_tree(b_nt, collapse_same_str=False, show_score=True)
    print '***************************************************'


def get_bracketed_repr(root_unary):
    return root_unary.get_bracketed_repr()


def display_tree(root_unary, show_span=False, collapse_same_str=True, show_score=False):
    global all_nonterminals
    print_dict = {}
    s_dict = {}
    reached_leaf = []
    children_stack = [(root_unary.span, root_unary)]
    while len(children_stack) > 0:
        print_nodes = []
        next_children_stack = []
        for cs, cn in children_stack:
            if cn.isChildTerminal:
                reached_leaf.append((cs, cn))
            else:
                print_nodes.append((cs, cn))
                next_children_stack += [(all_nonterminals[ccn_idx].span, all_nonterminals[ccn_idx]) for ccn_idx in
                                        cn.get_children()]

        print_nodes.sort()
        all_print_nodes = print_nodes + reached_leaf
        all_print_nodes.sort()
        # if show_span:
        span_line = '|'.join(
            [str(str(ps)).center(10) for ps, pn in all_print_nodes])
        # else:
        print_line = '|'.join([pn.phrase.encode('utf-8').center(pn.display_width) for ps, pn in all_print_nodes])

        print_line_num = print_dict.get(print_line, len(print_dict))
        span_line_num = print_line_num
        s_dict[span_line_num] = span_line
        print_dict[print_line, span_line] = print_line_num
        children_stack = next_children_stack
    # if show_span:
    span_leaf_line = '|'.join(
        [str(ps).center(10) for ps, pn in
         sorted(reached_leaf)])
    # else:
    leaf_line = '|'.join(
        [pn.german_phrase.encode('utf-8').center(pn.display_width) for ps, pn in sorted(reached_leaf)])

    s_dict[span_leaf_line] = len(s_dict)
    print_dict[leaf_line, span_leaf_line] = len(print_dict)
    out_str = ''
    out_span = ''
    for l, p, s in sorted([(l, p, s) for (p, s), l in print_dict.items()]):
        out_str += p + '\n'
        out_span += s + '\n'
    return out_str, out_span


def load_constituent_spans(cons_span_file):
    cs = {}
    if cons_span_file is not None:
        for l in open(cons_span_file, 'r').readlines():
            idx, sym, span_str = l.split('|||')
            k = tuple([int(i) for i in idx.split()])
            cs[k] = sym, span_str
    else:
        # no constituent spans
        pass
    return cs


def outside_score_prune(T_x, ref):
    global hard_prune, weight_outside_similarity
    S_x = []
    for t_x in T_x:
        if similarity_metric == "e":
            similarity_with_ref = (weight_outside_similarity * editdistance_prob(t_x.phrase, ref)) + t_x.score
        elif similarity_metric == "m":
            similarity_with_ref = ( weight_outside_similarity * meteor(t_x.phrase, ref)) + t_x.score
        S_x.append((similarity_with_ref, t_x))
    S_x.sort()
    T_x = [t for s, t in S_x[:hard_prune]]
    return T_x


if __name__ == '__main__':
    opt = OptionParser()
    opt.add_option("--ce", dest="en_corpus", default="data/moses-files/train.clean.tok.true.20.en",
                   help="english corpus sentences")
    opt.add_option("--cd", dest="de_corpus", default="data/moses-files/train.clean.tok.true.20.de",
                   help="german corpus sentences")
    opt.add_option("--st", dest="substr_trans", default="data/moses-files/substring-translations.20.tuned.ch.clean.en",
                   help="german corpus sentences")
    opt.add_option("--ss", dest="substr_spans", default="data/moses-files/train.clean.tok.true.20.de.span",
                   help="each line has a span and sent num")
    opt.add_option("--cs", dest="constituent_spans", default=None)
    opt.add_option("-d", dest="", default="data/moses-files/")
    opt.add_option("-o", dest="do_outside_prune", action="store_true", default=False, help="do outside prune")
    opt.add_option("-p", dest="hard_prune", type="int", default=1, help="prune applied per node")
    opt.add_option("-b", dest="show_bracketed", action="store_true", default=False,
                   help="show tree in bracketed notation")
    opt.add_option("-s", dest="show_span", action="store_true", default=False, help="show tree spans")
    opt.add_option("--lt", dest="inclm", action="store_true", default=False, help="increase lm score with height of tree")
    opt.add_option("-c", dest="use_parse_constituents", action="store_true", default=False,
                   help="use parse constituent")
    opt.add_option("--sim", dest="similarity_metric", default="e", help="e or m for editdistance or meteor")
    opt.add_option("--sw", dest="stopwords", default="data/moses-files/small_stopwords.txt")
    opt.add_option("--lm", dest="lm", default="data/moses-files/train.clean.tok.true.en.binary",
                   help="english language model file")
    opt.add_option("--wv", dest="word2vec", default="data/glove.6B.50d.txt", help="word2vec txt file")
    (options, _) = opt.parse_args()
    print options
    inclm = options.inclm
    print 'inclm', inclm
    en_sentences = codecs.open(options.en_corpus, 'r', 'utf-8').readlines()
    de_sentences = codecs.open(options.de_corpus, 'r', 'utf-8').readlines()
    substring_translations = read_substring_translations(options.substr_trans,
                                                         options.substr_spans, options.inclm)
    constituent_spans = load_constituent_spans(options.constituent_spans)
    similarity_metric = options.similarity_metric
    show_span = options.show_span
    show_bracketed = options.show_bracketed
    save_nltk_tree_img = False
    hard_prune = options.hard_prune
    stopwords = codecs.open(options.stopwords, 'r').read().split()
    lm_model = lm.LanguageModel(options.lm)
    ed = ED(options.word2vec)
    all_ds = []
    all_dt = []
    for sent_num in xrange(0, 20):
        en = en_sentences[sent_num].split()
        reference_root = ' '.join(en)
        de = de_sentences[sent_num].split()
        if len(en) < 25:
            binary_nodes = {}
            unary_nodes = {}
            all_nonterminals = {}

            # initialize unary nodes
            n = len(de)
            for i in xrange(0, n):
                E_x = get_single_word_translations(' '.join(de[i:i + 1]), sent_num, i)
                # E_x = get_substring_translations(sent_num, i, i)  # using substring translations
                unary_nodes[i, i] = E_x

            # for larger spans
            for span in xrange(1, n):
                for i in xrange(0, n - span):
                    k = i + span
                    if (sent_num, i, k) in constituent_spans or len(constituent_spans) == 0:
                        # print i, k, 'has constituent'
                        for j in xrange(i, k):
                            # print 'span size', span, 'start', i, 'mid', j, 'mid', j + 1, 'end', k
                            # print 'span gr', de[i:k], 'child1', de[i:j + 1], 'child2', de[j + 1:k + 1]
                            E_y = unary_nodes[i, j]
                            E_z = unary_nodes[j + 1, k]
                            E_x = get_combinations(E_y, E_z, de[i:k + 1])
                            bl = binary_nodes.get((i, k), [])
                            E_x += bl
                            binary_nodes[i, k] = E_x

                        if options.do_outside_prune:
                            binary_nodes[i, k] = outside_score_prune(binary_nodes[i, k], reference_root)
                        else:
                            binary_nodes[i, k] = sorted(binary_nodes[i, k], reverse=True)[:hard_prune]

                        if k == n - 1 and i == 0:
                            # when the span is the entire de sentence the "translation" is the reference en sentence
                            T_x = get_human_reference(' '.join(en))
                        else:
                            T_x = get_substring_translations(sent_num, i, k)
                        E_x = []
                        for t_x in T_x:
                            t_x = get_similarity(t_x, sorted(binary_nodes[i, k], reverse=True))
                            E_x.append(t_x)
                        ul = unary_nodes.get((i, k), [])
                        E_x += ul
                        unary_nodes[i, k] = E_x
                        assert len(unary_nodes[i, k]) <= hard_prune

                        if options.do_outside_prune:
                            unary_nodes[i, k] = outside_score_prune(unary_nodes[i, k], reference_root)
                    else:
                        binary_nodes[i, k] = []
                        unary_nodes[i, k] = []
                        pass
            closest_unary = unary_nodes[0, n - 1][0]

            dt, ds = display_tree(closest_unary)
            print dt
            if show_span:
                print ds
            if show_bracketed:
                print closest_unary.get_bracketed_repr(all_nonterminals)
            if save_nltk_tree_img:
                t = Tree.fromstring(closest_unary.get_bracketed_repr(all_nonterminals))
                t.draw()
                # tc = TreeWidget(cf.canvas(), t)
                # cf.add_widget(tc, 100, 100)
                # cf.print_to_file('parsetree.' + str(sent_num) + '.ps')
                # cf.destroy()
        pass
    #TODO: prune out phrase table ent where a german verb goes to null
    #TODO: merger lexical translation table into the phrase table (should fix all unknown word issues) from grow-diag-final
    #TODO: lm score gets stronger at higer parts of the tree 
    #TODO: do not use clean when creating  the txtspan
    
