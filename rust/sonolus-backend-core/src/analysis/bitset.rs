//! Dense bit sets used by the analyses (PORT.md T2.1).
//!
//! Fixed capacity, `Vec<u64>`-backed, deterministic ascending iteration —
//! suitable anywhere set contents can reach output (invariant §3.5).

/// A fixed-capacity dense bit set over `0..capacity`.
///
/// All index-taking methods panic on out-of-range indices (the capacity is
/// fixed at construction).
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct BitSet {
    words: Vec<u64>,
}

impl BitSet {
    /// An empty set with capacity for `capacity` elements.
    pub fn new(capacity: usize) -> Self {
        Self {
            words: vec![0; capacity.div_ceil(64)],
        }
    }

    pub fn insert(&mut self, i: usize) {
        self.words[i / 64] |= 1 << (i % 64);
    }

    pub fn remove(&mut self, i: usize) {
        self.words[i / 64] &= !(1 << (i % 64));
    }

    pub fn contains(&self, i: usize) -> bool {
        self.words[i / 64] & (1 << (i % 64)) != 0
    }

    pub fn clear(&mut self) {
        self.words.fill(0);
    }

    pub fn is_empty(&self) -> bool {
        self.words.iter().all(|&w| w == 0)
    }

    /// Number of set bits.
    pub fn count(&self) -> usize {
        self.words.iter().map(|w| w.count_ones() as usize).sum()
    }

    /// `self |= other`; returns true if `self` changed. Capacities must match.
    pub fn union_with(&mut self, other: &Self) -> bool {
        debug_assert_eq!(self.words.len(), other.words.len());
        let mut changed = false;
        for (w, &o) in self.words.iter_mut().zip(&other.words) {
            let new = *w | o;
            changed |= new != *w;
            *w = new;
        }
        changed
    }

    /// `self -= other`. Capacities must match.
    pub fn subtract(&mut self, other: &Self) {
        debug_assert_eq!(self.words.len(), other.words.len());
        for (w, &o) in self.words.iter_mut().zip(&other.words) {
            *w &= !o;
        }
    }

    /// `self &= other`; returns true if `self` changed. Capacities must match.
    pub fn intersect_with(&mut self, other: &Self) -> bool {
        debug_assert_eq!(self.words.len(), other.words.len());
        let mut changed = false;
        for (w, &o) in self.words.iter_mut().zip(&other.words) {
            let new = *w & o;
            changed |= new != *w;
            *w = new;
        }
        changed
    }

    /// Set bits in ascending order.
    pub fn iter(&self) -> BitSetIter<'_> {
        BitSetIter {
            words: &self.words,
            next_word: 0,
            current: 0,
        }
    }
}

impl<'a> IntoIterator for &'a BitSet {
    type Item = usize;
    type IntoIter = BitSetIter<'a>;

    fn into_iter(self) -> BitSetIter<'a> {
        self.iter()
    }
}

/// Ascending iterator over the set bits of a [`BitSet`].
#[derive(Debug, Clone)]
pub struct BitSetIter<'a> {
    words: &'a [u64],
    /// Index of the next word to load; `current` came from word `next_word - 1`.
    next_word: usize,
    current: u64,
}

impl Iterator for BitSetIter<'_> {
    type Item = usize;

    fn next(&mut self) -> Option<usize> {
        loop {
            if self.current != 0 {
                let bit = self.current.trailing_zeros() as usize;
                self.current &= self.current - 1;
                return Some((self.next_word - 1) * 64 + bit);
            }
            if self.next_word == self.words.len() {
                return None;
            }
            self.current = self.words[self.next_word];
            self.next_word += 1;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn insert_remove_contains() {
        let mut s = BitSet::new(130);
        assert!(s.is_empty());
        for i in [0, 63, 64, 129] {
            s.insert(i);
            assert!(s.contains(i));
        }
        assert_eq!(s.count(), 4);
        s.remove(64);
        assert!(!s.contains(64));
        assert_eq!(s.iter().collect::<Vec<_>>(), vec![0, 63, 129]);
        s.clear();
        assert!(s.is_empty());
    }

    #[test]
    fn union_and_subtract() {
        let mut a = BitSet::new(100);
        let mut b = BitSet::new(100);
        a.insert(1);
        a.insert(70);
        b.insert(70);
        b.insert(99);
        assert!(a.union_with(&b));
        assert_eq!(a.iter().collect::<Vec<_>>(), vec![1, 70, 99]);
        assert!(!a.union_with(&b), "second union changes nothing");
        a.subtract(&b);
        assert_eq!(a.iter().collect::<Vec<_>>(), vec![1]);
    }

    #[test]
    fn intersect() {
        let mut a = BitSet::new(100);
        let mut b = BitSet::new(100);
        a.insert(1);
        a.insert(70);
        b.insert(70);
        b.insert(99);
        assert!(a.intersect_with(&b));
        assert_eq!(a.iter().collect::<Vec<_>>(), vec![70]);
        assert!(!a.intersect_with(&b), "second intersect changes nothing");
    }

    #[test]
    fn zero_capacity_is_fine() {
        let s = BitSet::new(0);
        assert!(s.is_empty());
        assert_eq!(s.iter().count(), 0);
    }
}
