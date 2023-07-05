"""Module for running ALIGNN-FF."""
from ase.md import MDLogger
from jarvis.core.atoms import Atoms as JarvisAtoms
import os
from ase.md.nvtberendsen import NVTBerendsen
from ase.md.nptberendsen import NPTBerendsen
from ase.io import Trajectory
import matplotlib.pyplot as plt
from jarvis.analysis.thermodynamics.energetics import unary_energy
from ase.md.velocitydistribution import MaxwellBoltzmannDistribution
from ase.optimize import BFGS
from ase.optimize.bfgslinesearch import BFGSLineSearch
from ase.optimize.fire import FIRE
from ase.optimize.gpmin.gpmin import GPMin
from ase.optimize.lbfgs import LBFGS, LBFGSLineSearch
from ase.optimize.mdmin import MDMin
from ase.constraints import ExpCellFilter
from ase.eos import EquationOfState
from ase.units import kJ
from ase.optimize.sciopt import SciPyFminBFGS, SciPyFminCG
from ase.md import VelocityVerlet
from ase import units
from ase.md import Langevin
from ase.md.npt import NPT
from ase.md.andersen import Andersen
import ase.calculators.calculator
from ase.stress import full_3x3_to_voigt_6_stress
from jarvis.db.jsonutils import loadjson
from alignn.graphs import Graph
from alignn.models.alignn_atomwise import ALIGNNAtomWise, ALIGNNAtomWiseConfig
from jarvis.analysis.defects.vacancy import Vacancy
import numpy as np
from alignn.pretrained import get_prediction
from jarvis.analysis.structure.spacegroup import (
    Spacegroup3D,
    symmetrically_distinct_miller_indices,
)
from jarvis.analysis.interface.zur import make_interface
from jarvis.analysis.defects.surface import Surface
from jarvis.core.kpoints import Kpoints3D as Kpoints

# from jarvis.core.atoms import get_supercell_dims
from ase import Atoms as AseAtoms
from ase.phonons import Phonons
import matplotlib.pyplot as plt  # noqa
from jarvis.db.figshare import get_jid_data
from ase.cell import Cell


try:
    from gpaw import GPAW, PW
except Exception:
    pass
plt.switch_backend("agg")
# from ase.optimize.optimize import Optimizer
# from ase.io import Trajectory
# from ase.neighborlist import NeighborList

__author__ = "Kamal Choudhary, Brian DeCost, Keith Butler, Lily Major"


def default_path():
    """Get default model path."""
    dpath = os.path.abspath(
        str(os.path.join(os.path.dirname(__file__), "alignnff_wt10"))
    )
    print("model_path", dpath)
    return dpath


def revised_path():
    """Get defaukt model path."""
    dpath = os.path.abspath(
        str(os.path.join(os.path.dirname(__file__), "revised"))
    )
    print("model_path", dpath)
    return dpath


def mlearn_path():
    """Get model trained on mlearn path."""
    dpath = os.path.abspath(
        str(os.path.join(os.path.dirname(__file__), "fmult_mlearn_only"))
    )
    print("model_path", dpath)
    return dpath


def fd_path():
    """Get model trained on mlearn path."""
    dpath = os.path.abspath(
        str(os.path.join(os.path.dirname(__file__), "alignnff_fd"))
    )
    print("model_path", dpath)
    return dpath


def wt01_path():
    """Get defaukt model path."""
    dpath = os.path.abspath(
        str(os.path.join(os.path.dirname(__file__), "alignnff_wt01"))
    )
    print("model_path", dpath)
    return dpath


def wt1_path():
    """Get defaukt model path."""
    dpath = os.path.abspath(
        str(os.path.join(os.path.dirname(__file__), "alignnff_wt1"))
    )
    print("model_path", dpath)
    return dpath


def wt10_path():
    """Get defaukt model path."""
    dpath = os.path.abspath(
        str(os.path.join(os.path.dirname(__file__), "alignnff_wt10"))
    )
    print("model_path", dpath)
    return dpath


# print("default_model_path", default_model_path)


def ase_to_atoms(ase_atoms):
    """Convert ASE Atoms to JARVIS."""
    return JarvisAtoms(
        lattice_mat=ase_atoms.get_cell(),
        elements=ase_atoms.get_chemical_symbols(),
        coords=ase_atoms.get_positions(),
        #         pbc=True,
        cartesian=True,
    )


ignore_bad_restart_file = ase.calculators.calculator.Calculator._deprecated


class AlignnAtomwiseCalculator(ase.calculators.calculator.Calculator):
    """Module for ASE Calculator interface."""

    def __init__(
        self,
        restart=None,
        ignore_bad_restart_file=ignore_bad_restart_file,
        label=None,
        include_stress=True,
        atoms=None,
        directory=".",
        device=None,
        path=".",
        model_filename="best_model.pt",
        config_filename="config.json",
        keep_data_order=False,
        classification_threshold=None,
        batch_size=None,
        epochs=None,
        output_dir=None,
        stress_wt=1.0,
        force_multiplier=1.0,
        force_mult_natoms=True,
        **kwargs,
    ):
        """Initialize class."""
        super(AlignnAtomwiseCalculator, self).__init__(
            restart, ignore_bad_restart_file, label, atoms, directory, **kwargs
        )
        self.device = device
        self.include_stress = include_stress
        self.stress_wt = stress_wt
        config = loadjson(os.path.join(path, config_filename))
        self.config = config
        self.force_multiplier = force_multiplier
        self.force_mult_natoms = force_mult_natoms
        # config = TrainingConfig(**config)
        # if type(config) is dict:
        #    try:
        #        config = TrainingConfig(**config)
        #    except Exception as exp:
        #        print("Check", exp)
        if self.include_stress:
            self.implemented_properties = ["energy", "forces", "stress"]
            if config["model"]["stresswise_weight"] == 0:
                config["model"]["stresswise_weight"] = 0.1
        else:
            self.implemented_properties = ["energy", "forces"]

        config["keep_data_order"] = keep_data_order
        if classification_threshold is not None:
            config["classification_threshold"] = float(
                classification_threshold
            )
        if output_dir is not None:
            config["output_dir"] = output_dir
        if batch_size is not None:
            config["batch_size"] = int(batch_size)
        if epochs is not None:
            config["epochs"] = int(epochs)

        config["model.output_features"] = 1

        import torch

        if self.device is None:
            self.device = torch.device(
                "cuda" if torch.cuda.is_available() else "cpu"
            )
        model = ALIGNNAtomWise(ALIGNNAtomWiseConfig(**config["model"]))
        # model = ALIGNNAtomWise(config.model)
        model.state_dict()
        model.load_state_dict(
            torch.load(
                os.path.join(path, model_filename), map_location=self.device
            )
        )
        model.to(device)
        model.eval()

        self.net = model
        self.net.to(self.device)

    def calculate(self, atoms, properties=None, system_changes=None):
        """Calculate properties."""
        j_atoms = ase_to_atoms(atoms)
        num_atoms = j_atoms.num_atoms
        g, lg = Graph.atom_dgl_multigraph(j_atoms)
        result = self.net((g.to(self.device), lg.to(self.device)))
        # print ('stress',result["stress"].detach().numpy())
        if self.force_mult_natoms:
            mult = num_atoms
        else:
            mult = 1
        # print('result["stresses"]',result["stresses"],result["stresses"].shape)
        self.results = {
            "energy": result["out"].detach().cpu().numpy() * num_atoms,
            "forces": result["grad"].detach().cpu().numpy()
            * mult
            * self.force_multiplier,
            "stress": full_3x3_to_voigt_6_stress(
                result["stresses"][:3].reshape(3, 3).detach().cpu().numpy()
            )
            * self.stress_wt
            # * num_atoms,
            / 160.21766208,
            "dipole": np.zeros(3),
            "charges": np.zeros(len(atoms)),
            "magmom": 0.0,
            "magmoms": np.zeros(len(atoms)),
        }


class ForceField(object):
    """Module to run ALIGNN-FF."""

    def __init__(
        self,
        jarvis_atoms=None,
        model_path="out",
        model_filename="best_model.pt",
        include_stress=True,
        timestep=None,
        print_format=None,
        logger=None,
        logfile="alignn_ff.log",
        dyn=None,
        communicator=None,
        stress_wt=1.0,
        force_multiplier=1.0,
        force_mult_natoms=True,
    ):
        """Intialize class."""
        self.jarvis_atoms = jarvis_atoms
        self.model_path = model_path
        self.model_filename = model_filename
        self.include_stress = include_stress
        self.timestep = timestep
        self.atoms = self.jarvis_atoms.ase_converter()
        self.print_format = print_format
        self.dyn = dyn
        self.communicator = communicator
        self.logger = logger
        self.stress_wt = stress_wt
        self.force_multiplier = force_multiplier
        self.force_mult_natoms = force_mult_natoms
        if self.timestep is None:
            self.timestep = 0.01
        # Convert in appropriate units
        self.timestep = self.timestep * units.fs
        self.logfile = logfile
        if self.print_format is None:
            self.print_format = self.example_print
        if self.logger is None:
            self.logger = MDLogger(
                self.dyn,
                self.atoms,
                self.logfile,
                stress=False,
                peratom=False,
                header=True,
                mode="w",
            )
        # print ('STRUCTURE PROVIDED:')
        # print (ase_to_atoms(self.atoms))
        # print ()
        self.atoms.set_calculator(
            AlignnAtomwiseCalculator(
                path=self.model_path,
                include_stress=self.include_stress,
                model_filename=self.model_filename,
                stress_wt=self.stress_wt,
                force_multiplier=self.force_multiplier,
                force_mult_natoms=self.force_mult_natoms,
                # device="cuda" if torch.cuda.is_available() else "cpu",
            )
        )

    def example_print(self):
        """Print info."""
        if isinstance(self.atoms, ExpCellFilter):
            self.atoms = self.atoms.atoms
        line = ""
        try:
            line = f"time={self.dyn.get_time() / units.fs: 5.0f} fs "
        except Exception:
            pass
        line += (
            f"a={self.atoms.get_cell()[0,0]: 3.3f} Ang "
            + f"b={self.atoms.get_cell()[1,1]: 3.3f} Ang "
            + f"c={self.atoms.get_cell()[2,2]: 3.3f} Ang "
            + f"Volume={self.atoms.get_volume(): 3.3f} amu/a3 "
            + f"PE={self.atoms.get_potential_energy(): 5.5f} eV "
            + f"KE={self.atoms.get_kinetic_energy(): 5.5f} eV "
            + f"T={self.atoms.get_temperature(): 3.3f} K "
            # + f" P={atoms.
            # get_isotropic_pressure(atoms.get_stress()): 5.3f} bar "
        )
        print(line)

    def set_momentum_maxwell_boltzmann(self, temperature_K=10):
        """Set initial temperature."""
        print("SETTING INITIAL TEMPERATURE K", temperature_K)
        MaxwellBoltzmannDistribution(self.atoms, temperature_K=temperature_K)

    def unrelaxed_atoms(self):
        """Get energy of a system."""
        pe = self.atoms.get_potential_energy()
        fs = self.atoms.get_forces()
        # ke = self.atoms.get_kinetic_energy()
        # print("pe", pe)
        return pe, fs

    def optimize_atoms(
        self,
        optimizer="FIRE",
        trajectory="opt.traj",
        logfile="opt.log",
        steps=1000,
        fmax=0.05,
        optimize_lattice=True,
        interval=1,
    ):
        """Optimize structure."""
        available_ase_optimizers = {
            "BFGS": BFGS,
            "LBFGS": LBFGS,
            "LBFGSLineSearch": LBFGSLineSearch,
            "FIRE": FIRE,
            "MDMin": MDMin,
            "GPMin": GPMin,
            "SciPyFminCG": SciPyFminCG,
            "SciPyFminBFGS": SciPyFminBFGS,
            "BFGSLineSearch": BFGSLineSearch,
        }

        optimizer = available_ase_optimizers.get(optimizer, None)
        if optimizer is None:
            raise ValueError("Check optimizer", optimizer)
        if optimize_lattice:
            self.atoms = ExpCellFilter(self.atoms)
        print("OPTIMIZATION")
        self.dyn = optimizer(
            self.atoms, trajectory="opt.traj", logfile="opt.log"
        )
        self.dyn.attach(self.print_format, interval=interval)
        self.dyn.run(fmax=fmax, steps=steps)
        return (
            ase_to_atoms(self.atoms),
            self.atoms.get_potential_energy(),
            self.atoms.get_forces(),
        )

    def run_nve_velocity_verlet(
        self,
        filename="ase_nve",
        interval=1,
        steps=1000,
        initial_temperature_K=None,
    ):
        """Run NVE."""
        print("NVE VELOCITY VERLET")
        if initial_temperature_K is not None:
            self.set_momentum_maxwell_boltzmann(
                temperature_K=initial_temperature_K
            )
        self.dyn = VelocityVerlet(self.atoms, self.timestep)
        # Create monitors for logfile and a trajectory file
        # logfile = os.path.join(".", "%s.log" % filename)
        trajfile = os.path.join(".", "%s.traj" % filename)
        trajectory = Trajectory(trajfile, "w", self.atoms)
        # Attach monitors to trajectory
        self.dyn.attach(self.logger, interval=interval)
        self.dyn.attach(self.print_format, interval=interval)
        self.dyn.attach(trajectory.write, interval=interval)
        self.dyn.run(steps)
        return ase_to_atoms(self.atoms)

    def run_nvt_langevin(
        self,
        filename="ase_nvt_langevin",
        interval=1,
        temperature_K=300,
        steps=1000,
        friction=1e-4,
        initial_temperature_K=None,
    ):
        """Run NVT."""
        print("NVT LANGEVIN")
        if initial_temperature_K is not None:
            self.set_momentum_maxwell_boltzmann(
                temperature_K=initial_temperature_K
            )
        self.dyn = Langevin(
            self.atoms,
            self.timestep,
            temperature_K=temperature_K,
            friction=friction,
            communicator=self.communicator,
        )
        # Create monitors for logfile and a trajectory file
        # logfile = os.path.join(".", "%s.log" % filename)
        trajfile = os.path.join(".", "%s.traj" % filename)
        trajectory = Trajectory(trajfile, "w", self.atoms)
        # Attach monitors to trajectory
        self.dyn.attach(self.logger, interval=interval)
        self.dyn.attach(self.print_format, interval=interval)
        self.dyn.attach(trajectory.write, interval=interval)
        self.dyn.run(steps)
        return ase_to_atoms(self.atoms)

    def run_nvt_andersen(
        self,
        filename="ase_nvt_andersen",
        interval=1,
        temperature_K=300,
        steps=1000,
        andersen_prob=1e-1,
        initial_temperature_K=None,
    ):
        """Run NVT."""
        print("NVT ANDERSEN")
        if initial_temperature_K is not None:
            self.set_momentum_maxwell_boltzmann(
                temperature_K=initial_temperature_K
            )
        self.dyn = Andersen(
            self.atoms,
            self.timestep,
            temperature_K=temperature_K,
            andersen_prob=andersen_prob,
            communicator=self.communicator,
        )
        # Create monitors for logfile and a trajectory file
        # logfile = os.path.join(".", "%s.log" % filename)
        trajfile = os.path.join(".", "%s.traj" % filename)
        trajectory = Trajectory(trajfile, "w", self.atoms)
        # Attach monitors to trajectory
        self.dyn.attach(self.logger, interval=interval)
        self.dyn.attach(self.print_format, interval=interval)
        self.dyn.attach(trajectory.write, interval=interval)
        self.dyn.run(steps)
        return ase_to_atoms(self.atoms)

    def run_nvt_berendsen(
        self,
        filename="ase_nvt_berendsen",
        interval=1,
        temperature_K=300,
        steps=1000,
        taut=None,
        initial_temperature_K=None,
    ):
        """Run NVT."""
        print("NVT BERENDSEN")
        if initial_temperature_K is not None:
            self.set_momentum_maxwell_boltzmann(
                temperature_K=initial_temperature_K
            )
        if taut is None:
            taut = 100 * self.timestep
        self.dyn = NVTBerendsen(
            self.atoms,
            self.timestep,
            temperature_K=temperature_K,
            taut=taut,
            communicator=self.communicator,
        )
        # Create monitors for logfile and a trajectory file
        # logfile = os.path.join(".", "%s.log" % filename)
        trajfile = os.path.join(".", "%s.traj" % filename)
        trajectory = Trajectory(trajfile, "w", self.atoms)
        # Attach monitors to trajectory
        self.dyn.attach(self.logger, interval=interval)
        self.dyn.attach(self.print_format, interval=interval)
        self.dyn.attach(trajectory.write, interval=interval)
        self.dyn.run(steps)
        return ase_to_atoms(self.atoms)

    def run_npt_berendsen(
        self,
        filename="ase_npt_berendsen",
        interval=1,
        temperature_K=300,
        steps=1000,
        taut=49.11347394232032,
        taup=98.22694788464064,
        pressure=None,
        compressibility=None,
        initial_temperature_K=None,
    ):
        """Run NPT."""
        print("NPT BERENDSEN")
        if initial_temperature_K is not None:
            self.set_momentum_maxwell_boltzmann(
                temperature_K=initial_temperature_K
            )
        self.dyn = NPTBerendsen(
            self.atoms,
            self.timestep,
            temperature_K=temperature_K,
            taut=taut,
            taup=taup,
            pressure=pressure,
            compressibility=compressibility,
            communicator=self.communicator,
        )
        # Create monitors for logfile and a trajectory file
        # logfile = os.path.join(".", "%s.log" % filename)
        trajfile = os.path.join(".", "%s.traj" % filename)
        trajectory = Trajectory(trajfile, "w", self.atoms)
        # Attach monitors to trajectory
        self.dyn.attach(self.logger, interval=interval)
        self.dyn.attach(self.print_format, interval=interval)
        self.dyn.attach(trajectory.write, interval=interval)
        self.dyn.run(steps)
        return ase_to_atoms(self.atoms)

    def run_npt_nose_hoover(
        self,
        filename="ase_npt_nose_hoover",
        interval=1,
        temperature_K=300,
        steps=1000,
        externalstress=0.0,
        taut=None,
        initial_temperature_K=None,
    ):
        """Run NPT."""
        print("NPT: Combined Nose-Hoover and Parrinello-Rahman dynamics")
        if initial_temperature_K is not None:
            self.set_momentum_maxwell_boltzmann(
                temperature_K=initial_temperature_K
            )
        if taut is None:
            taut = 100 * self.timestep
        self.dyn = NPT(
            self.atoms,
            self.timestep,
            temperature_K=temperature_K,
            externalstress=externalstress,
        )
        # Create monitors for logfile and a trajectory file
        # logfile = os.path.join(".", "%s.log" % filename)
        trajfile = os.path.join(".", "%s.traj" % filename)
        trajectory = Trajectory(trajfile, "w", self.atoms)
        # Attach monitors to trajectory
        self.dyn.attach(self.logger, interval=interval)
        self.dyn.attach(self.print_format, interval=interval)
        self.dyn.attach(trajectory.write, interval=interval)
        self.dyn.run(steps)
        return ase_to_atoms(self.atoms)


def ev_curve(
    atoms=None,
    dx=np.arange(-0.05, 0.05, 0.01),
    model_path=".",
    model_filename="best_model.pt",
    fig_name="eos.png",
    on_relaxed_struct=False,
):
    """Get EV curve."""
    if on_relaxed_struct:
        ff = ForceField(
            jarvis_atoms=atoms,
            model_path=model_path,
            model_filename=model_filename,
        )
        relaxed, en, fs = ff.optimize_atoms()
    else:
        relaxed = atoms
    y = []
    vol = []
    for i in dx:
        s1 = relaxed.strain_atoms(i)
        ff = ForceField(
            jarvis_atoms=s1,
            model_path=model_path,
            model_filename=model_filename,
        )
        energy, fs = ff.unrelaxed_atoms()
        y.append(energy)
        vol.append(s1.volume)
    x = np.array(dx)
    y = np.array(y)
    eos = EquationOfState(vol, y, eos="murnaghan")
    v0, e0, B = eos.fit()
    kv = B / kJ * 1.0e24  # , 'GPa')
    # print("KV", kv)
    # eos.plot(show=True)
    eos.plot(fig_name)
    print("E", y)
    print("V", vol)
    plt.close()
    return x, y, eos, kv


def vacancy_formation(
    atoms=None,
    jid="",
    dataset="dft_3d",
    on_conventional_cell=False,
    enforce_c_size=15,
    extend=1,
    model_path=".",
    model_filename="best_model.pt",
    using_wyckoffs=True,
    on_relaxed_struct=True,
):
    """Get vacancy energy."""
    if atoms is None:
        from jarvis.db.figshare import data

        dft_3d = data(dataset)
        for i in dft_3d:
            if i["jid"] == jid:
                atoms = JarvisAtoms.from_dict(i["atoms"])
    if on_relaxed_struct:
        ff = ForceField(
            jarvis_atoms=atoms,
            model_path=model_path,
            model_filename=model_filename,
        )
        relaxed, en, fs = ff.optimize_atoms()
    else:
        relaxed = atoms

    strts = Vacancy(relaxed).generate_defects(
        on_conventional_cell=on_conventional_cell,
        enforce_c_size=enforce_c_size,
        extend=extend,
    )
    mem = []
    for j in strts:
        strt = JarvisAtoms.from_dict(j.to_dict()["defect_structure"])
        name = (
            str(jid)
            + "_"
            + str(strt.composition.reduced_formula)
            + "_"
            + j.to_dict()["symbol"]
            + "_"
            + j.to_dict()["wyckoff_multiplicity"]
        )
        print(name)
        bulk_atoms = JarvisAtoms.from_dict(j.to_dict()["atoms"])
        ff = ForceField(
            jarvis_atoms=bulk_atoms,
            model_path=model_path,
            model_filename=model_filename,
        )
        # energy, fs = ff.unrelaxed_atoms()
        relaxed, energy, fs = ff.optimize_atoms()
        # Bulk EPA
        pred_bulk_energy = energy / bulk_atoms.num_atoms
        defective_atoms = strt
        # print ('defective_atoms',strt)
        # defective_energy = i["defective_energy"]
        # pred_def_energy = (
        #    atom_to_energy(atoms=defective_atoms, only_energy=only_energy)
        #    * defective_atoms.num_atoms
        # )
        ff = ForceField(
            jarvis_atoms=defective_atoms,
            model_path=model_path,
            model_filename=model_filename,
        )
        relaxed, pred_def_energy, fs = ff.optimize_atoms()
        # pred_def_energy, fs = ff.unrelaxed_atoms()

        chem_pot = unary_energy(j.to_dict()["symbol"].replace(" ", ""))
        # print('pred_def_energy',pred_def_energy)
        symb = j.to_dict()["symbol"].replace(" ", "")
        # print('chem_pot',symb,unary_energy(symb))
        Ef = (
            pred_def_energy
            - (defective_atoms.num_atoms + 1) * pred_bulk_energy
            + chem_pot
        )
        # print (name,Ef2,j.to_dict()["symbol"])
        info = {}
        info["jid"] = jid
        info["symb"] = symb
        info["E_vac"] = Ef
        info["wyckoff"] = j.to_dict()["wyckoff_multiplicity"]
        mem.append(info)
    return mem


def surface_energy(
    atoms=None,
    jid="",
    on_conventional_cell=True,
    dataset="dft_3d",
    max_index=None,
    miller_index=[1, 1, 1],
    on_relaxed_struct=True,
    model_path=".",
    thickness=25,
    model_filename="best_model.pt",
):
    """Get surface energy."""
    if atoms is None:
        from jarvis.db.figshare import data

        dft_3d = data(dataset)
        for i in dft_3d:
            if i["jid"] == jid:
                atoms = JarvisAtoms.from_dict(i["atoms"])
    if on_relaxed_struct:
        ff = ForceField(
            jarvis_atoms=atoms,
            model_path=model_path,
            model_filename=model_filename,
        )
        atoms, en, fs = ff.optimize_atoms()
    if on_conventional_cell:
        atoms_cvn = Spacegroup3D(atoms).conventional_standard_structure
    else:
        atoms_cvn = atoms
    # energy = atom_to_energy(atoms=atoms_cvn, only_energy=only_energy)
    if max_index is not None:
        indices = symmetrically_distinct_miller_indices(
            max_index=max_index, cvn_atoms=atoms_cvn
        )
    else:
        indices = [miller_index]
    # indices = [[0, 0, 1]]
    ff = ForceField(
        jarvis_atoms=atoms_cvn,
        model_path=model_path,
        model_filename=model_filename,
    )
    # en, fs = ff.unrelaxed_atoms()
    relaxed, en, fs = ff.optimize_atoms()
    epa = en / atoms_cvn.num_atoms
    # epa = energy  # / atoms_cvn.num_atoms
    mem = []
    for j in indices:
        strt = Surface(
            atoms=atoms_cvn, indices=j, thickness=thickness
        ).make_surface()

        name = (
            str(strt.composition.reduced_formula)
            + "_"
            + str("_".join(map(str, j)))
        )

        # info_def = get_energy(strt, relax_atoms=False)
        ff = ForceField(
            jarvis_atoms=strt,
            model_path=model_path,
            model_filename=model_filename,
        )
        # energy, fs = ff.unrelaxed_atoms()  # / atoms_cvn.num_atoms
        relaxed, energy, fs = ff.optimize_atoms()

        m = np.array(strt.lattice_mat)
        surf_area = np.linalg.norm(np.cross(m[0], m[1]))
        surf_en = 16 * (energy - epa * (strt.num_atoms)) / (2 * surf_area)

        # print("Surface name", name, surf_en)
        info = {}
        info["name"] = name
        info["surf_en"] = surf_en

        mem.append(info)

        # return name, def_en,epa
    return mem


def get_interface_energy(
    film_atoms=None,
    subs_atoms=None,
    film_index=[1, 1, 1],
    subs_index=[0, 0, 1],
    film_thickness=25,
    subs_thickness=25,
    model_path="",
    seperation=3.0,
    vacuum=8.0,
    max_area_ratio_tol=1.00,
    max_area=500,
    ltol=0.05,
    atol=1,
    apply_strain=False,
    from_conventional_structure=True,
    gpaw_verify=False,
):
    """Get work of adhesion."""
    film_surf = Surface(
        film_atoms,
        indices=film_index,
        from_conventional_structure=from_conventional_structure,
        thickness=film_thickness,
        vacuum=vacuum,
    ).make_surface()
    subs_surf = Surface(
        subs_atoms,
        indices=subs_index,
        from_conventional_structure=from_conventional_structure,
        thickness=subs_thickness,
        vacuum=vacuum,
    ).make_surface()
    het = make_interface(
        film=film_surf,
        subs=subs_surf,
        seperation=seperation,
        vacuum=vacuum,
        max_area_ratio_tol=max_area_ratio_tol,
        max_area=max_area,
        ltol=ltol,
        atol=atol,
        apply_strain=apply_strain,
    )
    """
    print('film')
    print(het['film_sl'])
    print('subs')
    print(het['subs_sl'])
    print('intf')
    print(het['interface'])
    """
    a = get_prediction(
        atoms=het["film_sl"], model_name="jv_optb88vdw_total_energy_alignn"
    )[0]
    b = get_prediction(
        atoms=het["subs_sl"], model_name="jv_optb88vdw_total_energy_alignn"
    )[0]
    c = get_prediction(
        atoms=het["interface"], model_name="jv_optb88vdw_total_energy_alignn"
    )[0]
    print(het["interface"])
    m = het["interface"].lattice.matrix
    area = np.linalg.norm(np.cross(m[0], m[1]))
    wa = -16 * (c - b - a) / area
    print("Only alignn", wa)
    ff = ForceField(
        jarvis_atoms=het["film_sl"],
        model_path=model_path,
    )
    film_dat = ff.optimize_atoms(optimizer="FIRE", optimize_lattice=False)
    film_en = film_dat[1]

    ff = ForceField(
        jarvis_atoms=het["subs_sl"],
        model_path=model_path,
    )
    subs_dat = ff.optimize_atoms(optimizer="FIRE", optimize_lattice=False)
    subs_en = subs_dat[1]

    ff = ForceField(
        jarvis_atoms=het["interface"],
        model_path=model_path,
    )
    intf_dat = ff.optimize_atoms(optimizer="FIRE", optimize_lattice=True)
    intf_en = intf_dat[1]
    m = het["interface"].lattice.matrix
    area = np.linalg.norm(np.cross(m[0], m[1]))
    # should be positive Wad
    intf_energy = -16 * (intf_en - subs_en - film_en) / (area)  # J/m2
    info = {}

    info["interface_energy"] = intf_energy
    info["unoptimized_interface"] = het["interface"].to_dict()
    info["optimized_interface"] = intf_dat[0].to_dict()
    if gpaw_verify:
        name = "film"
        calc = GPAW(
            mode=PW(300),  # cutoff
            kpts=(2, 2, 1),  # k-points
            txt=name + ".txt",
        )
        film_ase = het["film_sl"].ase_converter()
        film_ase.calc = calc
        film_en = film_ase.get_potential_energy()
        print("film_en_gpaw", film_en)

        name = "subs"
        calc = GPAW(
            mode=PW(300),  # cutoff
            kpts=(2, 2, 1),  # k-points
            txt=name + ".txt",
        )
        subs_ase = het["subs_sl"].ase_converter()
        subs_ase.calc = calc
        subs_en = subs_ase.get_potential_energy()
        print("subs_en_gpaw", subs_en)

        name = "intf"
        calc = GPAW(
            mode=PW(300),  # cutoff
            kpts=(2, 2, 1),  # k-points
            txt=name + ".txt",
        )
        intf_ase = het["interface"].ase_converter()
        intf_ase.calc = calc
        intf_en_gpaw = intf_ase.get_potential_energy()
        print("intf_en_gpaw", subs_en)
        intf_gpaw = -16 * (intf_en_gpaw - subs_en - film_en) / (area)  # J/m2
        print("Wad gpaw", intf_gpaw)
    info["film_sl"] = het["film_sl"].to_dict()
    info["subs_sl"] = het["subs_sl"].to_dict()
    return info


def phonons(
    atoms=None,
    enforce_c_size=8,
    line_density=5,
    model_path=".",
    model_filename="best_model.pt",
    on_relaxed_struct=False,
    dim=[2, 2, 2],
    freq_conversion_factor=33.356,
):
    """Make Phonon calculation setup."""
    calc = AlignnAtomwiseCalculator(
        path=model_path,
        force_mult_natoms=False,
        force_multiplier=1,
        stress_wt=-4800,
    )

    from phonopy import Phonopy
    from phonopy.file_IO import (
        write_FORCE_CONSTANTS,
    )

    kpoints = Kpoints().kpath(atoms, line_density=line_density)
    bulk = atoms.phonopy_converter()
    phonon = Phonopy(bulk, [[dim[0], 0, 0], [0, dim[1], 0], [0, 0, dim[2]]])
    phonon.generate_displacements(distance=0.01)
    print("Len dis", len(phonon.supercells_with_displacements))
    supercells = phonon.get_supercells_with_displacements()
    # Force calculations by calculator
    set_of_forces = []
    disp = 0

    for scell in supercells:
        ase_atoms = AseAtoms(
            symbols=scell.get_chemical_symbols(),
            scaled_positions=scell.get_scaled_positions(),
            cell=scell.get_cell(),
            pbc=True,
        )
        ase_atoms.calc = calc
        # energy = ase_atoms.get_potential_energy()
        forces = np.array(ase_atoms.get_forces())
        disp = disp + 1

        drift_force = forces.sum(axis=0)
        for force in forces:
            force -= drift_force / forces.shape[0]
        set_of_forces.append(forces)
    phonon.produce_force_constants(forces=set_of_forces)
    write_FORCE_CONSTANTS(
        phonon.get_force_constants(), filename="FORCE_CONSTANTS"
    )

    freqs = []
    for k in kpoints.kpts:
        tmp = []
        for i, freq in enumerate(phonon.get_frequencies(k)):
            # print("[Phonopy]%3d: %10.5f cm-1" %  (i + 1, freq*33.356))#THz
            tmp.append(freq * freq_conversion_factor)
        freqs.append(tmp)

    freqs = np.array(freqs)
    for i in range(freqs.shape[1]):
        plt.plot(freqs[:, i], c="b")
    lbls = kpoints.labels
    lbls_x = np.arange(len(lbls))
    x_i = []
    x_j = []
    for i, j in zip(lbls_x, lbls):
        if j != "":
            tmp = "$" + str(j) + "$"
            x_j.append(tmp)
            x_i.append(i)
    plt.xticks(x_i, x_j)
    # plt.xticks(lbls_x,lbls)
    plt.tight_layout()
    plt.savefig("phonopy_bands.png")
    plt.close()

    phonon.run_mesh([20, 20, 20])
    phonon.run_total_dos()
    tdos = phonon._total_dos

    # print('tods',tdos._frequencies.shape)
    freqs, ds = tdos.get_dos()
    # print('tods',tdos.get_dos())
    dosfig = phonon.plot_total_dos()
    dosfig.savefig("phonopy_dos.png")
    dosfig.close()

    plt.plot(freqs, ds)
    plt.close("dos2.png")


def phonons3(
    atoms=None,
    enforce_c_size=8,
    line_density=5,
    model_path=".",
    model_filename="best_model.pt",
    on_relaxed_struct=False,
    dim=[2, 2, 2],
):
    """Make Phonon3 calculation setup."""
    from phono3py import Phono3py

    calc = AlignnAtomwiseCalculator(path=model_path)

    # kpoints = Kpoints().kpath(atoms, line_density=line_density)
    # dim = get_supercell_dims(cvn, enforce_c_size=enforce_c_size)
    # atoms = cvn.make_supercell([dim[0], dim[1], dim[2]])
    bulk = atoms.phonopy_converter()
    phonon = Phono3py(bulk, [[dim[0], 0, 0], [0, dim[1], 0], [0, 0, dim[2]]])
    phonon.generate_displacements(distance=0.03)
    # disps = phonon.generate_displacements()
    supercells = phonon.supercells_with_displacements
    print(
        "Len dis", len(phonon.supercells_with_displacements), len(supercells)
    )
    # Force calculations by calculator
    set_of_forces = []
    disp = 0

    for ii, scell in enumerate(supercells):
        print("scell=", ii)
        ase_atoms = AseAtoms(
            symbols=scell.get_chemical_symbols(),
            scaled_positions=scell.get_scaled_positions(),
            cell=scell.get_cell(),
            pbc=True,
        )
        ase_atoms.calc = calc
        # energy = ase_atoms.get_potential_energy()
        forces = np.array(ase_atoms.get_forces())
        disp = disp + 1
        drift_force = forces.sum(axis=0)
        for force in forces:
            force -= drift_force / forces.shape[0]
        set_of_forces.append(forces)
    # phonon.save("phono3py_disp.yaml")
    forces = np.array(set_of_forces).reshape(-1, len(phonon.supercell), 3)
    phonon.forces = forces
    phonon.produce_fc3()
    phonon.mesh_numbers = 30
    phonon.init_phph_interaction()
    phonon.run_thermal_conductivity(
        temperatures=range(0, 1001, 10), write_kappa=True
    )
    print(phonon.thermal_conductivity.kappa)


def ase_phonon(
    atoms=[],
    N=2,
    path=[],
    jid=None,
    npoints=100,
    dataset="dft_3d",
    delta=0.01,
    emin=-0.01,
    use_cvn=True,
    filename="Atom_phonon.png",
    ev_file=None,
    model_path="",
):
    """Get phonon bandstructure and DOS using ASE."""
    calc = AlignnAtomwiseCalculator(path=model_path)
    # Setup crystal and EMT calculator
    # atoms = bulk("Al", "fcc", a=4.05)

    # Phonon calculator
    # N = 7
    # ev_file = (None,)
    if jid is not None:
        atoms = JarvisAtoms.from_dict(
            get_jid_data(jid=jid, dataset=dataset)["atoms"]
        )
        filename = (
            jid + "_" + atoms.composition.reduced_formula + "_phonon.png"
        )
    if use_cvn:
        spg = Spacegroup3D(atoms)
        atoms_cvn = spg.conventional_standard_structure
        # lat_sys = spg.lattice_system
    else:
        atoms_cvn = atoms
    """
    if ev_file is not None:
        ev_curve(
            atoms=atoms_cvn,
            fig_name=ev_file,
            model_path=model_path,
            dx=np.arange(-0.2, 0.2, 0.05),
        )
        plt.clf()
        plt.close()
    """
    cell = Cell(atoms_cvn.lattice_mat)
    path = cell.bandpath(npoints=npoints)
    print(path)
    atoms = atoms_cvn.ase_converter()

    ph = Phonons(atoms, calc, supercell=(N, N, N), delta=delta)
    # ph = Phonons(atoms, EMT(), supercell=(N, N, N), delta=0.05)
    ph.run()

    # Read forces and assemble the dynamical matrix
    ph.read(acoustic=True)
    ph.clean()

    # path = atoms.cell.bandpath("GXULGK", npoints=100)
    bs = ph.get_band_structure(path)

    dos = ph.get_dos(kpts=(20, 20, 20)).sample_grid(npts=npoints, width=1e-3)

    # Plot the band structure and DOS:
    fig = plt.figure(1, figsize=(7, 4))
    ax = fig.add_axes([0.12, 0.07, 0.67, 0.85])
    # ax = fig.add_axes([0.12, 0.07, 0.67, 0.85])
    # print (bs)
    emax = max(bs.energies.flatten()) + 0.01  # 0.1  # 0.035
    bs.plot(ax=ax, emin=emin, emax=emax, color="blue")
    dosax = fig.add_axes([0.8, 0.07, 0.17, 0.85])
    dosax.fill_between(
        dos.get_weights(),
        dos.get_energies(),
        y2=0,
        color=(0.2, 0.4, 0.6, 0.6),
        # color="grey",
        edgecolor="blue",
        lw=1,
        where=dos.get_energies() >= emin,
    )
    dosax.set_ylim(emin, emax)
    dosax.set_yticks([])
    dosax.set_xticks([])
    dosax.set_xlabel("DOS", fontsize=18)
    fig.savefig(filename)
    plt.close()
    return bs


if __name__ == "__main__":
    atoms = JarvisAtoms.from_dict(
        get_jid_data(jid="JVASP-867", dataset="dft_3d")["atoms"]
        # get_jid_data(jid="JVASP-816", dataset="dft_3d")["atoms"]
    )
    mlearn = mlearn_path()
    phonons(atoms=atoms, model_path=mlearn, enforce_c_size=3)
    phonons3(atoms=atoms, model_path=mlearn, enforce_c_size=3)
    ase_phonon(atoms=atoms, model_path=mlearn)

"""
if __name__ == "__main__":

    from jarvis.db.figshare import get_jid_data
    from jarvis.core.atoms import Atoms

    # atoms = Spacegroup3D(
    #   JarvisAtoms.from_dict(
    #       get_jid_data(jid="JVASP-816", dataset="dft_3d")["atoms"]
    #   )
    # ).conventional_standard_structure
    # atoms = JarvisAtoms.from_poscar("POSCAR")
    # atoms = atoms.make_supercell_matrix([2, 2, 2])
    # print(atoms)
    model_path = default_path()
    print("model_path", model_path)
    # atoms=atoms.strain_atoms(.05)
    # print(atoms)
    # ev = ev_curve(atoms=atoms, model_path=model_path)
    # surf = surface_energy(atoms=atoms, model_path=model_path)
    # print(surf)
    # vac = vacancy_formation(atoms=atoms, model_path=model_path)
    # print(vac)

    # ff = ForceField(
    #    jarvis_atoms=atoms,
    #    model_path=model_path,
    # )
    # en,fs = ff.unrelaxed_atoms()
    # print ('en',en)
    # print('fs',fs)
    # phonons(atoms=atoms)
    # phonons3(atoms=atoms)
    # ff.set_momentum_maxwell_boltzmann(temperature_K=300)
    # xx = ff.optimize_atoms(optimizer="FIRE")
    # print("optimized st", xx)
    # xx = ff.run_nve_velocity_verlet(steps=5)
    # xx = ff.run_nvt_langevin(steps=5)
    # xx = ff.run_nvt_andersen(steps=5)
    # xx = ff.run_npt_nose_hoover(steps=20000, temperature_K=1800)
    # print(xx)
    atoms_al = Atoms.from_dict(
        get_jid_data(dataset="dft_3d", jid="JVASP-816")["atoms"]
    )
    surf = surface_energy(atoms=atoms_al, model_path=model_path)
    # atoms_al2o3 = Atoms.from_dict(
    #    get_jid_data(dataset="dft_3d", jid="JVASP-32")["atoms"]
    # )
    # atoms_sio2 = Atoms.from_dict(
    #    get_jid_data(dataset="dft_3d", jid="JVASP-58349")["atoms"]
    # )
    # atoms_cu = Atoms.from_dict(
    #    get_jid_data(dataset="dft_3d", jid="JVASP-867")["atoms"]
    # )
    # atoms_cu2o = Atoms.from_dict(
    #    get_jid_data(dataset="dft_3d", jid="JVASP-1216")["atoms"]
    # )
    # atoms_graph = Atoms.from_dict(
    #    get_jid_data(dataset="dft_3d", jid="JVASP-48")["atoms"]
    # )
    # intf = get_interface_energy(
    #    film_atoms=atoms_cu,
    #    subs_atoms=atoms_cu2o,
    #    film_thickness=25,
    #    subs_thickness=25,
    #    model_path=model_path,
    #    seperation=4.5,
    #    subs_index=[1, 1, 1],
    #    film_index=[1, 1, 1],
    # )
    # print(intf)
    print(surf)
"""
