# -*- coding: utf-8 -*-
"""
@ PyCAMP  Python Causal Modeling of Pathways, a python implmentation for modeling
causal relationship bewtween cellular signaling proteins, particularly phosphorylated
proteins based on reverse phase protein array (RPPA) data.  This model is designed
to model the signal transduction through series of protein phosphorylation cascade,
in which phosphorylation of a protein often activate the protein, which in turn
lead to phosphorylation of other proteins.  This model represent
the phosphorylation state(s) and activation state of a protein separately such that the model
is capable of capture the fact that, at certain time, phosphorylation of a protein
can be decoupled by drug and inhibitors. 


Created on Wed Aug 14 19:16:25 2013

@author: Xinghua  Lu
"""

import networkx as nx
import numpy as np
from numpy import matlib
from rpy2 import robjects 
import  math, cPickle, re
from SigNetNode import SigNetNode
from StringIO import StringIO
from NamedMatrix import NamedMatrix

import rpy2.robjects.numpy2ri
rpy2.robjects.numpy2ri.activate()   # enable directly pass numpy arrary or matrix as arguments to rpy object
R = robjects.r                      # load R instance
R.library("glmnet")
glmnet = R('glmnet')                # make glmnet from R a callable python object

R.library("mixtools")
normalmixEM = R('normalmixEM')




class PyGibbCAMP:  
    ## Constructor
    #  @param nodeFile  A string of pathname of file containing nodes.  The 
    #                   name, type, measured
    #  @param edgeFile  A list of tuples, each containing a source and sink node 
    #                   of an edge
    #  @param dataMatrixFile  A string to data
    def __init__(self, nodeFile , dataMatrixFile , perturbMatrix = None, missingDataMatrix=None):
        self.network = None
        self.obsData = None
        perturbInstances = None
        self.nChains = 1
        
        self.dictPerturbEffect = {'AKT1' : [('GSK690693',	0), \
        ('GSK690693_GSK1120212', 0)], 'MAP2K1' : [('GSK690693_GSK1120212', 0)],\
        'EGFR': [('EGF' , 1), ('FGF1', 1)]}

        # parse data mastrix by calling NamedMatrix class
        if not dataMatrixFile:
            raise Exception("Cannot create PyCAMP obj without 'dataMatrixFile'")
            return
        self.obsData = NamedMatrix(dataMatrixFile)
        self.obsData.colnames = map(lambda s: s+'F', self.obsData.colnames)
        self.obsDataFileName = dataMatrixFile
        
        if perturbMatrix:        
            self.perturbData = NamedMatrix(perturbMatrix)
            perturbInstances = self.perturbData.getColnames()
                    
        if missingDataMatrix:
            self.missingDataMatrix = NamedMatrix(missingDataMatrix)
            self.missingDataMatrix.colnames = map(lambda s: s+'F', self.missingDataMatrix.colnames)

        if not nodeFile:
            raise Exception("Calling 'intiNetwork' with empty nodeFile name")
            return

        try:
            nf = open(nodeFile, "r")
            nodeLines = nf.readlines()
            if len(nodeLines) == 1:  # Mac files end a line with \r instead of \n
                nodeLines = nodeLines[0].split("\r")
            nf.close()
        except IOError:
            raise Exception( "Failed to open the file containing nodes")
            return
            
        print "Creating network"          
        self.network = nx.DiGraph()

        self.dictProteinToAntibody = dict()
        self.dictAntibodyToProtein = dict()
        # parse nodes
        for line in nodeLines:
            #print line
            protein, antibody = line.rstrip().split(',')
            if protein not in self.dictProteinToAntibody:
                self.dictProteinToAntibody[protein] = []
            self.dictProteinToAntibody[protein].append(antibody)
            self.dictAntibodyToProtein[antibody] = protein
            
            fluo = antibody + 'F'
            if protein not in self.network:
                self.network.add_node(protein, nodeObj = SigNetNode(protein, 'activeState', False))
            self.network.add_node(antibody, nodeObj= SigNetNode(antibody, 'phosState', False))
            self.network.add_node(fluo, nodeObj = SigNetNode(fluo, 'fluorescence', True))
            self.network.add_edge(antibody, protein)
            self.network.add_edge(antibody, fluo)
        
        for perturb in perturbInstances:
            self.network.add_node(perturb, nodeObj = SigNetNode(perturb, 'perturbation', True))                
            
        # Add edges between perturbation, protein activity,and  phosphorylation layers 
        for pro in self.dictProteinToAntibody:
            for phos in self.dictAntibodyToProtein:
                if self.dictAntibodyToProtein[phos] == pro:
                    continue
                self.network.add_edge(pro, phos)
            #for perturb in perturbInstances:
             #   self.network.add_edge(perturb, pro)
            
        
    ## Init parameters of the model
    #  In Bayesian network setting, the joint probability is calculated
    #  through the product of a series conditional probability.  The parameters
    #  of the PyCAMP model defines p(x | Pa(X)).  For observed fluorescent node
    #  the conditional probability is a mixture of two Gaussian distribution.  
    #  therefore, the parameters are two pairs of mu and sigma.  For
    #  the hidden variables representing phosphorylation states and activation
    #  states of proteins, the conditional probability is defined by a logistic
    #  regression. Therefore, the parameters associated with such a node is a 
    #  vector of real numbers.
    # 
    def _initParams(self):
        print "Initialize parameters associated with each node in each MCMC chain"
        for nodeId in self.network: 
            self._initNodeParams(nodeId)
            
    def _initNodeParams(self, nodeId):
        nodeObj = self.network.node[nodeId]['nodeObj']
        if nodeObj.type == 'fluorescence':                
            # Estimate mean and sd of fluo signal using mixture model
            if nodeId in self.missingDataMatrix.getColnames():
                nodeData = self.obsData.getValuesByCol( nodeId)
                nodeData = nodeData[self.missingDataMatrix.getValuesByCol(nodeId) == 0]
                #print str(nodeData)
            else:
                nodeData = self.obsData.getValuesByCol(nodeId)
                
            mixGaussians = normalmixEM(robjects.FloatVector(nodeData), k = 2 )
            # mus and sigmas are represented as nChain x 2 matrices
            nodeObj.mus = matlib.repmat(np.array(mixGaussians[2]), self.nChains, 1)
            sigmas = np.array(mixGaussians[3])            
            sigmas[np.where(sigmas < 0.1)] = .5
            nodeObj.sigmas = matlib.repmat(sigmas, self.nChains, 1)
        else:
            preds = self.network.predecessors(nodeId)
            if len(preds) > 0:
                nodeObj.paramNames = preds
                nodeObj.params = np.random.randn(self.nChains, len(preds) + 1)
            else:
                nodeObj.params  = None
                
    
    ## Initialize latent variables
    #    
    #
    def _initHiddenStates(self):
        hiddenNodes = [n for n in self.network if not self.network.node[n]['nodeObj'].bMeasured]
        nCases, nAntibody = self.obsData.shape()
        caseNames = self.obsData.getRownames()
        
        self.nodeStates = list()
        for c in range(self.nChains):
            tmp = np.zeros((nCases, len(hiddenNodes)))
            tmp[np.random.rand(nCases, len(hiddenNodes)) < 0.4] = 1
            tmp = np.column_stack((tmp, self.perturbData.data))
            colnames = hiddenNodes + self.perturbData.colnames
            self.nodeStates.append(NamedMatrix(npMatrix = tmp, colnames = colnames, rownames = caseNames))
        
        
    ## Calculate the marginal probability of observing the measured data by
    #  integrating out all possible setting of latent variable states and 
    #  model parameters.
    def calcEvidenceLikelihood(self):
        # this can be easily achieved by taking expectation of observed 
        # phosphorylation states 
        loglikelihood = 0        
        obsNodes = [n for n in self.network if self.network.node[n]['nodeObj'].type == 'fluorescence']
        for c in range(self.nChains):
            for nodeId in obsNodes:
                curNodeData = self.obsData.getValuesByCol(nodeId)
                pred = self.network.predecessors(nodeId)
                predStates = self.nodeStates[c].getValuesByCol(pred)
                
                nodeObj = self.network.node[nodeId]['nodeObj'] # where parameters are saved
                loglikelihood += np.sum( predStates * (- math.log(math.sqrt(2 * 3.14)) - np.log(nodeObj.sigmas[c, 1])\
                - 0.5 * np.square(curNodeData - nodeObj.mus[c, 1]) / np.square(nodeObj.sigmas[c, 1])) \
                + (1 - predStates) *  (- math.log(math.sqrt(2 * 3.14)) - np.log(nodeObj.sigmas[c, 0])\
                - 0.5 * np.square(curNodeData - nodeObj.mus[c, 0]) / np.square(nodeObj.sigmas[c, 0])))

        loglikelihood /= self.nChains
        return loglikelihood
            

    ## Perform graph search
    def trainGibbsEM(self, nChains = 10, alpha = 0.1, nParents = 10, nSamples = 5, pickleDumpFile = None, maxIter = 1000):
        self.nChains = nChains
        self.alpha = alpha  
        self.likelihood = list()
        self.nSamples = nSamples
        self.nParents = nParents
        if pickleDumpFile:
            self.pickleDumpFile = pickleDumpFile
        else:
            self.pickleDumpFile = self.obsDataFileName + "alpha" + str(self.alpha) +  ".pickle"  
        
        # Starting EM set up Markov chains  to train a model purely based on prior knowledge
        self._initHiddenStates()
        self._initParams()

        # perform update of latent variables in a layer-wise manner
        self.likelihood = list()        
        
        self.expectedStates = list()
        nCases, nAntibodies = np.shape(self.obsData.data)
        for c in range(self.nChains):                  
            # each chain collect expected statistics of nodes from samples along the chain
            self.expectedStates.append(np.zeros(np.shape(self.nodeStates[c].data)))

        print "Starting EM: alpha = " + str(self.alpha) + "; nChains = " + str(self.nChains) + "; nSamples = " + str (self.nSamples)
        optLikelihood = float("-inf")
        bConverged = False
        sampleCount = 0
        
        likelihood = self.calcEvidenceLikelihood()
        print "nIter: 0"  + "; log likelihood of evidence: " + str(likelihood)
        self.likelihood.append(likelihood)

        for nIter in range(maxIter): 
            #if nIter > 0 and (nIter % 100) == 0:
            #    self.nParents -= 1
                
            # E-step of EM
            self._updateStates()            
            if  (nIter+1) % 2 == 0: # we collect sample every other iteration
                sampleCount += 1
                for c in range(self.nChains):
                    self.expectedStates[c] +=  self.nodeStates[c].data                
                
            # M-step of EM.  We only update parameters after a collecting a certain number of samples
            if sampleCount >= self.nSamples:
                sampleCount = 0
                 # take expectation of sample states
                self.expectedStates = map(lambda x: x / self.nSamples, self.expectedStates)
                self._updteParams(self.alpha)
                
                likelihood = self.calcEvidenceLikelihood()
                self.likelihood.append(likelihood)   
                print "nIter: " + str(nIter + 1) + "; log likelihood of evidence: " + str(likelihood)                    

                # collect the current best fit models
                if likelihood > optLikelihood:
                    optLikelihood = likelihood
                    try:
                        cPickle.dump(self, open(self.pickleDumpFile, 'wb'))
                    except: 
                        raise Exception("Cannot create pickle dumpfile " + self.pickleDumpFile)

                bConverged = self._checkConvergence()
                if bConverged:
                    print "EM converged!"
                    break
                
                for c in range(self.nChains):  # clear expectedStates
                    self.expectedStates[c] = np.zeros(np.shape(self.nodeStates[c].data))
                
        # now try to delete edges that does contribute to evidence
        self.trimEdgeByConsensus(.9)
        return self  
            
    def _checkConvergence(self):
        # To do, add convergence checking code
        if len(self.likelihood) < 20:
            return False
            
        ml = np.mean(self.likelihood[-5:-1])
        ratio = abs(self.likelihood[-1] - ml ) / abs(ml)        
        return ratio <= 0.001

    def _updateActivationStates(self):
        nCases, antibody = np.shape(self.obsData.data)
        nCases, nHiddenNodes = np.shape(self.nodeStates[0].data)

        # interate through all nodes. 
        for c in range(self.nChains):
            activationNode = [n for n in self.network if self.network.node[n]['nodeObj'].type == 'activeState']
            for nodeId in activationNode:            
                nodeObj = self.network.node[nodeId]['nodeObj']
                # skip observed nodes
                if nodeObj.bMeasured:  # all other observed variables will not be sampled
                    continue
                
                curNodeMarginal = self.calcNodeCondProb(nodeId, c)
                
                # sample states of current node based on the prob, and update 
                sampleState = np.zeros(nCases)
                sampleState[curNodeMarginal >= np.random.rand(nCases)] = 1.
                curNodeIndx = self.nodeStates[c].findColIndices(nodeId)
                self.nodeStates[c].data[:, curNodeIndx] = sampleState
                
                # clamp the activationState of perturbed nodes to a fix value
                if nodeId in self.dictPerturbEffect:
                    # the diction keeps a list conditins under which the node is perurbed and the state to be clamped to
                    for condition, state in self.dictPerturbEffect[nodeId]:
                        perturbState = self.nodeStates[c].getValuesByCol(condition)
                        indx = self.nodeStates[c].findColIndices(nodeId)
                        self.nodeStates[c].data[perturbState==1, indx] = state
                        
        
    def _updateStates(self):
        nCases, antibody = np.shape(self.obsData.data)
        nCases, nHiddenNodes = np.shape(self.nodeStates[0].data)
        varWithMissingValues = self.missingDataMatrix.getColnames()
        
        # interate through all nodes. 
        for c in range(self.nChains):
            # update missing fluo signals for certain proteins
            fluoNodes = [n for n in self.network if self.network.node[n]['nodeObj'].type == 'fluorescence']
            for nodeId in fluoNodes:
                nodeObj = self.network.node[nodeId]['nodeObj']
                # sample the missing fluo experiment data for cases with missing value                         
                if nodeId in varWithMissingValues:
                    #print "processing node with missing values: " + nodeId
                    nodeIndx = self.obsData.findColIndices(nodeId)
                    missingCases = self.missingDataMatrix.getValuesByCol(nodeId) == 1
                    pred = self.network.predecessors(nodeId)[0]
                    predStates = self.nodeStates[c].getValuesByCol(pred)[missingCases]
                    self.obsData.data[missingCases, nodeIndx] = \
                    predStates * (np.random.randn(len(predStates)) * nodeObj.sigmas[c, 1] + nodeObj.mus[c, 1]) \
                    + (1 - predStates) * (np.random.randn(len(predStates)) * nodeObj.sigmas[c, 0] + nodeObj.mus[c, 0])                       
            
            # update state layer-wise, first update the phosphorylation layer
            phosNodes = [n for n in self.network if self.network.node[n]['nodeObj'].type == 'phosState']
            for nodeId in phosNodes:            
                nodeObj = self.network.node[nodeId]['nodeObj']
                
                curNodeMarginal = self.calcNodeCondProb(nodeId, c)
                
                # sample states of current node based on the prob, and update 
                sampleState = np.zeros(nCases)
                sampleState[curNodeMarginal >= np.random.rand(nCases)] = 1.
                curNodeIndx = self.nodeStates[c].findColIndices(nodeId)
                self.nodeStates[c].data[:, curNodeIndx] = sampleState
                
            
    def calcNodeCondProb(self, nodeId, c):
        """
        Calculate the marginal probability of a node's state set to "1" conditioning 
        on all evidence.
        
        args:
             nodeId   A string id of the node of interest
             c        An integer indicate the chain from which the parameter 
                         vector to be used  
        """
        nodeObj = self.network.node[nodeId]['nodeObj']
        if nodeObj.bMeasured:
            raise Exception("Call _caclNodeMarginalProb on an observed variable " + nodeId)

        nCases, nAntibody = np.shape(self.obsData.data)        

        # collect the state of the predecessors of the node
        preds = self.network.predecessors(nodeId)        
        logProbOneCondOnParents = 0
        logProbZeroCondOnParents = 0
        if len(preds) > 0:  # if the node has parents  
            # calculate p(curNode = 1 | parents);                 
            nodeParams = nodeObj.params[c,:] 
            predStates =  np.column_stack((np.ones(nCases), self.nodeStates[c].getValuesByCol(preds))) 
            pOneCondOnParents = 1 / (1 + np.exp( - np.dot(predStates, nodeParams)))
            pOneCondOnParents[pOneCondOnParents == 1] -= np.finfo(np.float).eps
            pOneCondOnParents[pOneCondOnParents == 0] += np.finfo(np.float).eps
            logProbOneCondOnParents  = np.log(pOneCondOnParents)
            logProbZeroCondOnParents = np.log(1 - pOneCondOnParents)

        # collect  evidence from  children 
        logProbChildCondOne = 0  # the prob of child conditioning on current node == 1
        logProdOfChildCondZeros = 0
        
        children = self.network.successors(nodeId)
        if len(children) > 0:
            for child in children:   
                nodeObj = self.network.node[child]['nodeObj']
                if nodeObj.type == "fluorescence":
                    #print child + " mus: " + str(nodeObj.mus)
                    #print child + " sigmas: " + str(nodeObj.sigmas)
                    curChildData = self.obsData.getValuesByCol(child)  
                    # calculate the probability using mixture Gaussian
                    logProbChildCondOne =  ( - math.log( nodeObj.sigmas[c, 1]) - 0.5 * np.square(curChildData - nodeObj.mus[c, 1]) / np.square(nodeObj.sigmas[c,1])) 
                    logProdOfChildCondZeros = ( - math.log( nodeObj.sigmas[c, 0]) - 0.5 * np.square(curChildData - nodeObj.mus[c, 0]) / np.square(nodeObj.sigmas[c,0])) 
                    #print "logProbChildCondOne: " + str(logProbChildCondOne)
                    #print "logProdOfChildCondZeros: " + str(logProdOfChildCondZeros)
                else:  # current child is a latent variable, means current node is an activation node
                    # collect data and parameters associated with the node
                    curChildStates = self.nodeStates[c].getValuesByCol(child)                    
                    
                    # Collect states of the predecessors of the child
                    childPreds = self.network.predecessors(child)
                    childNodeParams = nodeObj.params[c,:]
                    childPredStates = self.nodeStates[c].getValuesByCol(childPreds)
                    childPredStates = np.column_stack((np.ones(nCases), childPredStates)) # padding data with a column ones as bias
                    
                    # Set the state of current node to ones 
                    curNodePosInPredList = childPreds.index(nodeId) + 1 # offset by 1 because padding 
                    if childNodeParams[curNodePosInPredList] == 0:  # not an real edge 
                        continue
                    childPredStates[:, curNodePosInPredList] = np.ones(nCases)                
                    pChildCondCurNodeOnes = 1 / (1 + np.exp(-np.dot(childPredStates, childNodeParams)))
                    pChildCondCurNodeOnes[pChildCondCurNodeOnes==1] -= np.finfo(np.float).eps
                    pChildCondCurNodeOnes[pChildCondCurNodeOnes==0] += np.finfo(np.float).eps
                    logProbChildCondOne += np.log (curChildStates * pChildCondCurNodeOnes + (1 - curChildStates) * (1 - pChildCondCurNodeOnes))
                    
                    # set the state of the current node (nodeId) to zeros 
                    childPredStates [:, curNodePosInPredList] = np.zeros(nCases)
                    pChildCondCurNodeZeros = 1 / (1 + np.exp(- np.dot(childPredStates, childNodeParams))) 
                    pChildCondCurNodeZeros[pChildCondCurNodeZeros==1]  -= np.finfo(np.float).eps
                    pChildCondCurNodeZeros[pChildCondCurNodeZeros==0]  += np.finfo(np.float).eps
                    logProdOfChildCondZeros += np.log(curChildStates * pChildCondCurNodeZeros + (1 - curChildStates) * (1 - pChildCondCurNodeZeros))

        # now we can calculate the marginal probability of current node 
        curNodeMarginal = 1 / (1 + np.exp(logProbZeroCondOnParents + logProdOfChildCondZeros - logProbOneCondOnParents - logProbChildCondOne))
        return curNodeMarginal
    

    def parseGlmnetCoef(self, glmnet_res):        
        """ Parse the 'beta' matrix returned by calling glmnet through RPy2.
            Return the first column of 'beta' matrix of the glmnet object 
            with 3 or more non-zero values 
            """
        # read in intercept; a vector of length of nLambda
        a0 = np.array(glmnet_res.rx('a0'))[0]
        
        # Read in lines of beta matrix txt, which is a nVariables * nLambda.
        # Since we call glmnet by padding x with a column of 1s, we only work
        # with the 'beta' matrix returned by fit
        betaLines = StringIO(str(glmnet_res.rx('beta'))).readlines()
        dimStr = re.search("\d+\s+x\s+\d+", betaLines[1]).group(0)
        if not dimStr:
            raise Exception("'parse_glmnet_res' could not determine the dims of beta")
        nVariables , nLambda = map(int, dimStr.split(' x ')) 
        betaMatrix = np.zeros( (nVariables, nLambda), dtype=np.float)
        
        # glmnet print beta matrix in mulitple blocks with 
        # nVariable * blockSize
        blockSize = len(betaLines[4].split()) - 1
        curBlockColStart = - blockSize
        for line in betaLines:  #read in blocks
            m = re.search('^V\d+', line)
            if not m:  # only find the lines begins with 'V\d'
                continue
            else:
                rowIndx = int(m.group(0)[1:len(m.group(0))]) 
            if rowIndx == 1:
                curBlockColStart += blockSize
                
            # set 'rowIndx' as start from 0
            rowIndx -= 1

            fields = line.rstrip().split()
            fields.pop(0)
            if len(fields) != blockSize:
                blockSize = len(fields)
            for j in range(blockSize):
                if fields[j] == '.':
                    continue
                else:
                    betaMatrix[rowIndx, curBlockColStart + j] = float(fields[j])                 
                            
        return a0, betaMatrix       
      
        
    def _updteParams(self, alpha = 0.1):
        # Update the parameter associated with each node, p(n | Pa(n)) using logistic regression,
        # using expected states of precessors as X and current node states acrss samples as y
        nCases, nVariables = np.shape(self.obsData.data)
        
        # update the parameters layer-wise 
        fluoNodes = [n for n in self.network if self.network.node[n]['nodeObj'].type == 'fluorescence']
        for nodeId in fluoNodes:            
            preds = self.network.predecessors(nodeId)
            predIndices = self.nodeStates[0].findColIndices(preds)
            nodeObj = self.network.node[nodeId]['nodeObj']            
            for c in range(self.nChains): 
                expectedPredState = self.expectedStates[c][:, predIndices]
                # take care of case that all states are set one state
                rIndx = map(lambda z: int(math.floor(z)), np.random.rand(50) * nCases)
                if sum(expectedPredState == 0) == nCases:
                    expectedPredState[rIndx] = 1
                elif sum(expectedPredState == 1) == nCases:
                    expectedPredState[rIndx] = 0
                    
                curNodeData = self.obsData.getValuesByCol(nodeId)
                nodeObj.mus[c,0] = np.mean ((1-expectedPredState) * curNodeData)
                nodeObj.sigmas[c, 0] = np.std ((1-expectedPredState) * curNodeData)
                nodeObj.mus[c, 1] = np.mean (expectedPredState * curNodeData)
                nodeObj.sigmas[c, 1] = np.std (expectedPredState * curNodeData)  

#                # we constrain that mu0 to be smaller than mu1s; otherwise toggle the state             
#                if nodeObj.mus[c,0] > nodeObj.mus[c,1]:
#                    tmp = nodeObj.mus[c,0]
#                    nodeObj.mus[c,0] = nodeObj.mus[c,1]
#                    nodeObj.mus[c,1] = tmp
#                    tmp = nodeObj.sigmas[c, 0]
#                    nodeObj.sigmas[c, 0] = nodeObj.sigmas[c, 1]
#                    nodeObj.sigmas[c, 1] = tmp
#                    predStates = self.nodeStates[c].data[:,predIndices] 
#                    predOnes =  predStates == 1                    
#                    self.nodeStates[c].data[predOnes.T[0], predIndices] = 0
#                    self.nodeStates[c].data[map(lambda x: not x, predOnes.T[0]), predIndices] = 1                    

        phosNodes = [n for n in self.network if self.network.node[n]['nodeObj'].type == 'phosState']
        for nodeId in phosNodes:            
            preds = self.network.predecessors(nodeId)
            predIndices = self.nodeStates[0].findColIndices(preds)
            nodeObj = self.network.node[nodeId]['nodeObj']            
            for c in range(self.nChains): 
                expectedPredState = self.expectedStates[c][:, predIndices]
                #x = np.column_stack((np.ones(nCases), expectedPredState))                    
                x =  np.column_stack((np.ones(nCases), expectedPredState))
                y = self.nodeStates[c].getValuesByCol(nodeId) 
                    
                #check if all x and y are of same value, which will lead to problem for glmnet
                rIndx = map(lambda z: int(math.floor(z)), np.random.rand(50) * nCases)
                if sum(y) == nCases:  # if every y == 1                      
                    y[rIndx] = 0                        
                elif sum( map(lambda x: 1 - x, y)) == nCases:
                    y[rIndx] = 1        
                y = robjects.vectors.IntVector(y)
                
                allRwoSumOnes = np.where(np.sum(x, 0) == nCases)[0]
                for col in allRwoSumOnes:
                    rIndx = map(lambda z: int(math.floor(z)), np.random.rand(3) * nCases)
                    x[rIndx, col] = 0 
                allZeros = np.where(np.sum(np.ones(np.shape(x)) - x, 0) == nCases) 
                for col in allZeros[0]:
                    rIndx = map(lambda z: int(math.floor(z)), np.random.rand(3) * nCases)
                    x[rIndx, col] = 1
                    
                # call logistic regression using glmnet from Rpy
                fit = glmnet (x, y, alpha = alpha, family = "binomial", intercept = 0)
                    
                # extract coefficients glmnet, keep the first set beta with nParent non-zeros values
                a0, betaMatrix = self.parseGlmnetCoef(fit) 
                for j in range(np.shape(betaMatrix)[1]):
                    if sum(betaMatrix[:, j] != 0.) > self.nParents:
                        break
                if j >= len(a0):
                    j = len(a0) - 1
                
                nodeObj.params[c,:] =  betaMatrix[:, j]

        actionNodes = [n for n in self.network if self.network.node[n]['nodeObj'].type == 'activeState']
        for nodeId in actionNodes:            
            preds = self.network.predecessors(nodeId)
            predIndices = self.nodeStates[0].findColIndices(preds)
            nodeObj = self.network.node[nodeId]['nodeObj']            
            for c in range(self.nChains): 
                expectedPredState = self.expectedStates[c][:, predIndices]
                #x = np.column_stack((np.ones(nCases), expectedPredState))                    
                x =  np.column_stack((np.ones(nCases), expectedPredState))
                y = self.nodeStates[c].getValuesByCol(nodeId) 
                    
                #check if all x and y are of same value, which will lead to problem for glmnet
                rIndx = map(lambda z: int(math.floor(z)), np.random.rand(50) * nCases)
                if sum(y) == nCases:  # if every y == 1                      
                    y[rIndx] = 0                        
                elif sum( map(lambda x: 1 - x, y)) == nCases:
                    y[rIndx] = 1        
                y = robjects.vectors.IntVector(y)
                
                allRwoSumOnes = np.where(np.sum(x, 0) == nCases)[0]
                for col in allRwoSumOnes:
                    rIndx = map(lambda z: int(math.floor(z)), np.random.rand(3) * nCases)
                    x[rIndx, col] = 0 
                allZeros = np.where(np.sum(np.ones(np.shape(x)) - x, 0) == nCases) 
                for col in allZeros[0]:
                    rIndx = map(lambda z: int(math.floor(z)), np.random.rand(3) * nCases)
                    x[rIndx, col] = 1
                    
                # call logistic regression using glmnet from Rpy
                fit = glmnet (x, y, alpha = alpha, family = "binomial", intercept = 0)
                    
                # extract coefficients glmnet, keep the first set beta with nParent non-zeros values
                a0, betaMatrix = self.parseGlmnetCoef(fit) 
                for j in range(np.shape(betaMatrix)[1]):
                    if sum(betaMatrix[:, j] != 0.) > self.nParents:
                        break
                if j >= len(a0):
                    j = len(a0) - 1
                
                nodeObj.params[c,:] =  betaMatrix[:, j]

    
    def trimEdgeByConsensus(self, percent):
        antibodies = [n for n in self.network if self.network[n]['nodeObj'].type == 'phosState']
        for node in antibodies:            
            preds = self.network.predecessors(node)
            if len(preds) > 0:
                nodeParams = self.network.node[node]['nodeObj'].params 
                nChain, nParams = np.shape(nodeParams)
            
                for i in range(1, nParams):
                    if sum(nodeParams[:,i]==0) > math.floor(nChain * percent):
                        self.network.remove_edge(preds[i-1], node)
            

    
    
